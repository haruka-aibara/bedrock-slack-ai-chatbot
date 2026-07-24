"""
lambda_function_bedrock_backend/lambda_function.py
This module handles processing text input through Amazon Bedrock and
sending responses back to Slack.
"""

import json
import logging
import os
import time
from typing import Any, NamedTuple

import boto3
from botocore.exceptions import ClientError
from slack_sdk import WebClient

from boto3_utils import get_bedrock_runtime_client

# Configure logger
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize clients
bedrock_runtime = get_bedrock_runtime_client()
slack_client = WebClient(token=os.environ.get("SLACK_BOT_TOKEN"))
dynamodb = boto3.resource("dynamodb")

# Get model ID from environment variable
_model_id = os.environ.get("BEDROCK_MODEL_ID")
if not _model_id:
    raise ValueError("BEDROCK_MODEL_ID environment variable is not set")
MODEL_ID: str = _model_id

# Get max tokens from environment variable with default fallback
MAX_TOKENS = int(os.environ.get("BEDROCK_MAX_TOKENS", "1000"))

# DynamoDB table for conversation history
_table_name = os.environ.get("DYNAMODB_TABLE_NAME")
if not _table_name:
    raise ValueError("DYNAMODB_TABLE_NAME environment variable is not set")
conversation_table = dynamodb.Table(_table_name)

# Keep at most this many messages (user+assistant pairs) to avoid token overflow
MAX_HISTORY_MESSAGES = 20

# Second cap on history, on total size rather than message count: twenty short turns
# and twenty pasted stack traces are very different amounts of context. Characters are
# a rough proxy for tokens, deliberately conservative so mixed-script threads still fit.
MAX_HISTORY_CHARACTERS = 24000

# Conversation TTL: 7 days
CONVERSATION_TTL_SECONDS = 7 * 24 * 60 * 60

# How stale a question may be and still be worth answering. An answer that lands long
# after the question was asked is noise in the thread, so past this point the bot says
# it gave up instead. Note this is deliberately shorter than the queue's visibility
# timeout: a redelivery is always too late to answer, and its job is to make sure the
# user is told something rather than left waiting on silence.
ANSWER_DEADLINE_SECONDS = 60

# Shown in-thread when a question aged out before it could be answered.
DEADLINE_MESSAGE = "回答に時間がかかりすぎたため中断しました。もう一度メンションしてください。"

# Idempotency claims are stored in the conversation table under a prefixed key. Slack
# thread timestamps are numeric, so a prefixed key can never collide with a real thread.
CLAIM_KEY_PREFIX = "event#"

# How long a claim stays valid. Matches the queue's visibility timeout: once SQS is
# willing to hand the message to another invocation, the previous claim is stale and
# must not keep the retry from running.
CLAIM_TTL_SECONDS = 180


class ConversationState(NamedTuple):
    """A thread's stored messages plus the version they were read at."""

    messages: list[dict[str, str]]
    version: int


def _claim_key(event_id: str) -> dict[str, str]:
    """Build the DynamoDB key that represents the claim on a Slack event."""
    return {"thread_ts": f"{CLAIM_KEY_PREFIX}{event_id}"}


def claim_event(event_id: str) -> bool:
    """Take an exclusive claim on a Slack event, returning False if one is already held.

    SQS delivers at least once, so the same question can arrive more than once. The
    first invocation to write this item wins and the others skip the work.

    A claim older than the queue's visibility timeout is treated as abandoned and can be
    taken again — otherwise an invocation killed mid-flight would leave a claim behind
    and the redelivery would silently swallow the user's question.
    """
    now = int(time.time())
    try:
        conversation_table.put_item(
            Item={
                **_claim_key(event_id),
                "claim_expires_at": now + CLAIM_TTL_SECONDS,
                "expires_at": now + CONVERSATION_TTL_SECONDS,
            },
            ConditionExpression="attribute_not_exists(thread_ts) OR claim_expires_at < :now",
            ExpressionAttributeValues={":now": now},
        )
        return True
    except ClientError as error:
        if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return False
        raise


def release_event_claim(event_id: str) -> None:
    """Give up the claim on an event so a redelivery is allowed to retry it."""
    conversation_table.delete_item(Key=_claim_key(event_id))


def get_conversation_history(thread_ts: str) -> ConversationState:
    """Retrieve conversation history for a thread from DynamoDB, with its version."""
    response = conversation_table.get_item(Key={"thread_ts": thread_ts})
    item = response.get("Item")
    if not item:
        return ConversationState(messages=[], version=0)
    return ConversationState(messages=item.get("messages", []), version=int(item.get("version", 0)))


def save_conversation_history(thread_ts: str, messages: list[dict[str, str]], expected_version: int) -> None:
    """Persist conversation history for a thread to DynamoDB with TTL.

    The write is conditional on the version last read. Two mentions racing in one thread
    would otherwise both read the same history and the slower write would erase the
    other's turn; instead the loser fails and lets SQS retry it against fresh state.
    """
    conversation_table.put_item(
        Item={
            "thread_ts": thread_ts,
            "messages": messages,
            "version": expected_version + 1,
            "expires_at": int(time.time()) + CONVERSATION_TTL_SECONDS,
        },
        ConditionExpression="attribute_not_exists(thread_ts) OR #version = :expected",
        ExpressionAttributeNames={"#version": "version"},
        ExpressionAttributeValues={":expected": expected_version},
    )


def trim_history(messages: list[dict[str, str]]) -> list[dict[str, str]]:
    """Keep only the most recent messages so long threads do not overflow the context.

    Two caps apply: the number of messages, and their combined size. Whichever bites
    first, the oldest turns are dropped.

    Bedrock rejects a conversation whose first message is not from the user, so a window
    that would start on an assistant reply drops that reply as well. The newest message
    is always kept, even when it alone exceeds the size cap.
    """
    trimmed = list(messages[-MAX_HISTORY_MESSAGES:])

    while len(trimmed) > 1 and sum(len(message["content"]) for message in trimmed) > MAX_HISTORY_CHARACTERS:
        trimmed = trimmed[1:]

    if trimmed and trimmed[0].get("role") != "user":
        trimmed = trimmed[1:]

    return trimmed


def generate_answer(messages: list[dict[str, str]]) -> str:
    """
    Generate a response using Amazon Bedrock with full conversation history.

    Uses the Converse API, which takes the message list directly instead of a
    model-specific request body, so switching models does not mean rewriting the call.

    Args:
        messages: List of {"role": "user"/"assistant", "content": "..."} dicts

    Returns:
        The generated response text
    """
    logger.info("Input messages: %s", messages)

    response = bedrock_runtime.converse(
        modelId=MODEL_ID,
        messages=[{"role": message["role"], "content": [{"text": message["content"]}]} for message in messages],
        inferenceConfig={"maxTokens": MAX_TOKENS},
    )
    logger.info("Received response from Bedrock, usage: %s", response.get("usage"))

    content = response["output"]["message"]["content"]
    output_text = "".join(block["text"] for block in content if "text" in block)
    if not output_text:
        raise ValueError("Bedrock returned no text content")

    logger.info("Output text: %s", output_text)
    return output_text


def lambda_handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """
    AWS Lambda function handler to process SQS events.

    Args:
        event: AWS Lambda event data from SQS
        _context: AWS Lambda context object

    Returns:
        Response with status code and message
    """
    body = json.loads(event["Records"][0]["body"])
    channel_id = body.get("channel_id")
    thread_ts = body.get("thread_ts")
    input_text = body.get("input_text")
    event_id = body.get("event_id") or ""

    logger.info("Processing request - channel_id: %s, thread_ts: %s", channel_id, thread_ts)
    logger.info("Processing request - input_text: %s", input_text)

    if not input_text or input_text.strip() == "":
        error_message = "入力テキストが空です。有効なテキストを入力してください。"
        slack_client.chat_postMessage(
            channel=channel_id,
            thread_ts=thread_ts,
            text=error_message,
        )
        return {"statusCode": 400, "body": json.dumps("Empty input text")}

    if event_id and not claim_event(event_id):
        logger.info("Skipping Slack event %s, already claimed by another invocation", event_id)
        return {"statusCode": 200, "body": json.dumps("Duplicate event skipped")}

    enqueued_at = body.get("enqueued_at")
    if enqueued_at is not None and int(time.time()) - int(enqueued_at) > ANSWER_DEADLINE_SECONDS:
        # Say so rather than answering late or dropping the question in silence. The
        # message is consumed, so the user is told exactly once.
        logger.warning("Question aged out after %ss, answering with the deadline message", ANSWER_DEADLINE_SECONDS)
        slack_client.chat_postMessage(channel=channel_id, thread_ts=thread_ts, text=DEADLINE_MESSAGE)
        return {"statusCode": 200, "body": json.dumps("Question expired")}

    try:
        # Load conversation history and append new user message
        state = get_conversation_history(thread_ts)
        history = trim_history([*state.messages, {"role": "user", "content": input_text}])

        # Generate response with full conversation context
        output_text = generate_answer(history)
        logger.info("Generated output_text: %s", output_text)

        # Reply in the same thread, then record the turn. Saving first would mean a
        # failed post retries against a history that already contains the answer, and
        # the user's question would be appended to the thread twice.
        slack_client.chat_postMessage(
            channel=channel_id,
            thread_ts=thread_ts,
            text=output_text,
        )
        save_conversation_history(
            thread_ts,
            [*history, {"role": "assistant", "content": output_text}],
            state.version,
        )
    except Exception:
        # Hand the message back to SQS by dropping the claim, so the retry is not
        # mistaken for a duplicate and skipped.
        if event_id:
            release_event_claim(event_id)
        raise

    return {"statusCode": 200, "body": json.dumps("Message sent successfully")}
