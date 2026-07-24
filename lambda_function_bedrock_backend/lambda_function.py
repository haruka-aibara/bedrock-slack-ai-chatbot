"""
lambda_function_bedrock_backend/lambda_function.py
This module handles processing text input through Amazon Bedrock and
sending responses back to Slack.

Conversation context comes from the Slack thread itself rather than from a store of
its own. The thread is what the user can see, so reading it is the only way the bot
can answer "what do you think?" about a discussion it was not part of — and it leaves
one source of truth instead of two that can disagree.
"""

import json
import logging
import os
import re
import time
from collections import deque
from typing import Any

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

# DynamoDB table holding idempotency claims. It stores no conversation state; Slack does.
_table_name = os.environ.get("DYNAMODB_TABLE_NAME")
if not _table_name:
    raise ValueError("DYNAMODB_TABLE_NAME environment variable is not set")
idempotency_table = dynamodb.Table(_table_name)

# Keep at most this many messages (user+assistant pairs) to avoid token overflow
MAX_HISTORY_MESSAGES = 20

# Second cap on history, on total size rather than message count: twenty short turns
# and twenty pasted stack traces are very different amounts of context. Characters are
# a rough proxy for tokens, deliberately conservative so mixed-script threads still fit.
MAX_HISTORY_CHARACTERS = 24000

# Bounds on reading a thread. Slack returns replies oldest first, so a long thread is
# paged through while only the newest messages are kept — those are the ones that
# survive trimming anyway.
THREAD_PAGE_SIZE = 200
MAX_THREAD_PAGES = 10
MAX_THREAD_MESSAGES = 200

# How long a claim record lives in DynamoDB before TTL removes it.
CLAIM_ITEM_TTL_SECONDS = 24 * 60 * 60

# How long a claim stays valid. Matches the queue's visibility timeout: once SQS is
# willing to hand the message to another invocation, the previous claim is stale and
# must not keep the retry from running.
CLAIM_TTL_SECONDS = 180

# How stale a question may be and still be worth answering. An answer that lands long
# after the question was asked is noise in the thread, so past this point the bot says
# it gave up instead. Note this is deliberately shorter than the queue's visibility
# timeout: a redelivery is always too late to answer, and its job is to make sure the
# user is told something rather than left waiting on silence.
ANSWER_DEADLINE_SECONDS = 60

# Shown in-thread when a question aged out before it could be answered.
DEADLINE_MESSAGE = "回答に時間がかかりすぎたため中断しました。もう一度メンションしてください。"

# Shown in-thread when the mention carried no question and the thread gave no context.
EMPTY_INPUT_MESSAGE = "入力テキストが空です。有効なテキストを入力してください。"

# The bot's own canned replies are filtered back out when reading a thread: feeding old
# failures back as context teaches the model to produce more of them.
CANNED_MESSAGES = frozenset({DEADLINE_MESSAGE, EMPTY_INPUT_MESSAGE})

# Slack renders <@U…> as a mention. Stripped from context so the model never echoes one
# back and pings somebody who was not part of the conversation.
MENTION_PATTERN = re.compile(r"<@[A-Z0-9]+>")


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
        idempotency_table.put_item(
            Item={
                "event_id": event_id,
                "claim_expires_at": now + CLAIM_TTL_SECONDS,
                "expires_at": now + CLAIM_ITEM_TTL_SECONDS,
            },
            ConditionExpression="attribute_not_exists(event_id) OR claim_expires_at < :now",
            ExpressionAttributeValues={":now": now},
        )
        return True
    except ClientError as error:
        if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return False
        raise


def release_event_claim(event_id: str) -> None:
    """Give up the claim on an event so a redelivery is allowed to retry it."""
    idempotency_table.delete_item(Key={"event_id": event_id})


def fetch_thread_messages(channel_id: str, thread_ts: str, latest_ts: str | None = None) -> list[dict[str, Any]]:
    """Read a Slack thread, oldest first, keeping only the newest messages.

    Messages posted after ``latest_ts`` are left out: while this question waited in the
    queue the thread may have moved on, and answering with context the asker had not
    written yet is confusing.

    Requires the bot token to hold a history scope for the conversation type
    (``channels:history`` for public channels, ``groups:history`` for private ones).
    """
    messages: deque[dict[str, Any]] = deque(maxlen=MAX_THREAD_MESSAGES)
    cursor: str | None = None

    for _ in range(MAX_THREAD_PAGES):
        response = slack_client.conversations_replies(
            channel=channel_id,
            ts=thread_ts,
            limit=THREAD_PAGE_SIZE,
            cursor=cursor,
        )
        messages.extend(response.get("messages") or [])

        cursor = (response.get("response_metadata") or {}).get("next_cursor")
        if not response.get("has_more") or not cursor:
            break
    else:
        logger.warning("Thread %s longer than %s pages, using the newest messages", thread_ts, MAX_THREAD_PAGES)

    if latest_ts is None:
        return list(messages)
    return [message for message in messages if float(message.get("ts", 0)) <= float(latest_ts)]


def message_text(message: dict[str, Any]) -> str:
    """Extract the usable text of a Slack message, or an empty string if it has none."""
    if message.get("subtype") in {"channel_join", "channel_leave", "thread_broadcast_tombstone"}:
        return ""
    text = MENTION_PATTERN.sub("", message.get("text") or "").strip()
    if text in CANNED_MESSAGES:
        return ""
    return text


def append_turn(turns: list[dict[str, str]], role: str, text: str) -> None:
    """Add a turn, folding it into the previous one when the speaker has not changed.

    Bedrock requires user and assistant turns to alternate, but a thread happily runs
    several human messages in a row, so consecutive messages become one turn.
    """
    if turns and turns[-1]["role"] == role:
        turns[-1]["content"] = f"{turns[-1]['content']}\n{text}"
    else:
        turns.append({"role": role, "content": text})


def build_conversation(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Turn Slack thread messages into the alternating conversation Bedrock expects.

    The bot's own messages become assistant turns and everything else becomes a user
    turn, so a discussion the bot only just joined still reads as a conversation.
    """
    turns: list[dict[str, str]] = []
    for message in messages:
        text = message_text(message)
        if not text:
            continue
        append_turn(turns, "assistant" if message.get("bot_id") else "user", text)
    return turns


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


def build_prompt_messages(channel_id: str, thread_ts: str, message_ts: str | None, input_text: str) -> list[dict[str, str]]:
    """Assemble the conversation to send to Bedrock for this mention.

    Falls back to the mention on its own if the thread cannot be read — a missing
    history scope should degrade the answer, not break the bot.
    """
    try:
        thread = fetch_thread_messages(channel_id, thread_ts, latest_ts=message_ts)
    except Exception:
        logger.warning("Could not read thread %s, answering from the mention alone", thread_ts, exc_info=True)
        thread = []

    turns = build_conversation(thread)

    # Slack reads are eventually consistent, so the mention that triggered this run may
    # not be in the thread yet. Without this the bot would answer the wrong message.
    # A bare mention adds nothing: an empty turn is not a question Bedrock will accept.
    if input_text.strip() and not any(message.get("ts") == message_ts for message in thread):
        append_turn(turns, "user", input_text)

    return trim_history(turns)


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
    message_ts = body.get("message_ts")
    input_text = body.get("input_text") or ""
    event_id = body.get("event_id") or ""

    logger.info("Processing request - channel_id: %s, thread_ts: %s", channel_id, thread_ts)
    logger.info("Processing request - input_text: %s", input_text)

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
        # A bare mention is still answerable when the thread has something to go on —
        # "what do you think?" is the whole point of reading the thread.
        messages = build_prompt_messages(channel_id, thread_ts, message_ts, input_text)

        if not messages:
            slack_client.chat_postMessage(channel=channel_id, thread_ts=thread_ts, text=EMPTY_INPUT_MESSAGE)
            return {"statusCode": 400, "body": json.dumps("Empty input text")}

        output_text = generate_answer(messages)
        logger.info("Generated output_text: %s", output_text)

        slack_client.chat_postMessage(
            channel=channel_id,
            thread_ts=thread_ts,
            text=output_text,
        )
    except Exception:
        # Hand the message back to SQS by dropping the claim, so the retry is not
        # mistaken for a duplicate and skipped.
        if event_id:
            release_event_claim(event_id)
        raise

    return {"statusCode": 200, "body": json.dumps("Message sent successfully")}
