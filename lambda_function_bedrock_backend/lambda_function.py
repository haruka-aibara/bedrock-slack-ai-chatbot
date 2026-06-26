"""
lambda_function_bedrock_backend/lambda_function.py
This module handles processing text input through Amazon Bedrock and
sending responses back to Slack.
"""

import json
import logging
import os
import time
from typing import Any

import boto3
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

# Conversation TTL: 7 days
CONVERSATION_TTL_SECONDS = 7 * 24 * 60 * 60


def get_conversation_history(thread_ts: str) -> list[dict[str, str]]:
    """Retrieve conversation history for a thread from DynamoDB."""
    response = conversation_table.get_item(Key={"thread_ts": thread_ts})
    item = response.get("Item")
    if not item:
        return []
    return item.get("messages", [])


def save_conversation_history(thread_ts: str, messages: list[dict[str, str]]) -> None:
    """Persist conversation history for a thread to DynamoDB with TTL."""
    conversation_table.put_item(
        Item={
            "thread_ts": thread_ts,
            "messages": messages,
            "expires_at": int(time.time()) + CONVERSATION_TTL_SECONDS,
        }
    )


def generate_answer(messages: list[dict[str, str]]) -> str:
    """
    Generate a response using Amazon Bedrock with full conversation history.

    Args:
        messages: List of {"role": "user"/"assistant", "content": "..."} dicts

    Returns:
        The generated response text
    """
    logger.info("Input messages: %s", messages)

    request_body = json.dumps(
        {
            "messages": messages,
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": MAX_TOKENS,
        }
    )

    response = bedrock_runtime.invoke_model(
        modelId=MODEL_ID,
        accept="application/json",
        contentType="application/json",
        body=request_body,
    )
    logger.info("Received response from Bedrock")

    response_body_raw = response.get("body")
    if response_body_raw is None:
        raise ValueError("Response body is None")
    response_body = json.loads(response_body_raw.read())
    output_text = response_body.get("content")[0].get("text")
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

    # Load conversation history and append new user message
    history = get_conversation_history(thread_ts)
    history.append({"role": "user", "content": input_text})

    # Trim to keep only the most recent messages
    if len(history) > MAX_HISTORY_MESSAGES:
        history = history[-MAX_HISTORY_MESSAGES:]

    # Generate response with full conversation context
    output_text = generate_answer(history)
    logger.info("Generated output_text: %s", output_text)

    # Append assistant response and save updated history
    history.append({"role": "assistant", "content": output_text})
    save_conversation_history(thread_ts, history)

    # Reply in the same thread
    slack_client.chat_postMessage(
        channel=channel_id,
        thread_ts=thread_ts,
        text=output_text,
    )

    return {"statusCode": 200, "body": json.dumps("Message sent successfully")}
