"""
lambda_function_bedrock_backend/lambda_function.py
This module handles processing text input through Amazon Bedrock and
sending responses back to Slack.
"""

import json
import logging
import os
from collections.abc import Callable
from typing import Any

from slack_sdk import WebClient

from boto3_utils import get_bedrock_runtime_client

# Configure logger
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize clients
bedrock_runtime = get_bedrock_runtime_client()
client = WebClient(token=os.environ.get("SLACK_BOT_TOKEN"))

# Get model ID from environment variable
_model_id = os.environ.get("BEDROCK_MODEL_ID")
if not _model_id:
    raise ValueError("BEDROCK_MODEL_ID environment variable is not set")
MODEL_ID: str = _model_id

# Get max tokens from environment variable with default fallback
MAX_TOKENS = int(os.environ.get("BEDROCK_MAX_TOKENS", "1000"))


def generate_answer(input_text: str) -> str:
    """
    Generate a response text using Amazon Bedrock with the provided input.

    Args:
        input_text: The text input to process

    Returns:
        The generated response text
    """
    # Set messages
    messages = [
        {
            "role": "user",
            "content": input_text,
        },
    ]
    logger.info("Input messages: %s", messages)

    # Set request body
    request_body = json.dumps(
        {
            "messages": messages,
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": MAX_TOKENS,
        }
    )
    logger.info("Request body: %s", request_body)

    # Call Bedrock API
    response = bedrock_runtime.invoke_model(
        modelId=MODEL_ID,
        accept="application/json",
        contentType="application/json",
        body=request_body,
    )
    logger.info("Received response from Bedrock")

    # Extract response text
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
        context: AWS Lambda context object

    Returns:
        Response with status code and message
    """
    # Extract information from SQS queue
    body = json.loads(event["Records"][0]["body"])
    channel_id = body.get("channel_id")
    input_text = body.get("input_text")

    logger.info("Processing request - channel_id: %s", channel_id)
    logger.info("Processing request - input_text: %s", input_text)

    # Check if input text is empty
    if not input_text or input_text.strip() == "":
        error_message = "入力テキストが空です。有効なテキストを入力してください。"
        client.chat_postMessage(
            channel=channel_id,
            text=error_message,
        )
        return {"statusCode": 400, "body": json.dumps("Empty input text")}

    # Generate response using Bedrock
    output_text = generate_answer(input_text)
    logger.info("Generated output_text: %s", output_text)

    # Send response to Slack
    client.chat_postMessage(
        channel=channel_id,
        text=output_text,
    )

    return {"statusCode": 200, "body": json.dumps("Message sent successfully")}
