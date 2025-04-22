"""
lambda_function_bedrock_backend/lambda_function.py
This module handles processing text input through Amazon Bedrock and
sending responses back to Slack.
"""

import json
import logging
import os
from typing import TYPE_CHECKING, Any, Dict

import boto3
from slack_sdk import WebClient

# Configure logger
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Type annotation for boto3 clients
if TYPE_CHECKING:
    from mypy_boto3_bedrock_runtime import BedrockRuntimeClient

    bedrock_runtime: BedrockRuntimeClient = boto3.client("bedrock-runtime")
else:
    bedrock_runtime = boto3.client("bedrock-runtime")

# Initialize Slack client
client = WebClient(token=os.environ.get("SLACK_BOT_TOKEN"))


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
            "max_tokens": 1000,
        }
    )
    logger.info("Request body: %s", request_body)

    # Call Bedrock API
    response = bedrock_runtime.invoke_model(
        modelId="apac.anthropic.claude-3-5-sonnet-20241022-v2:0",
        accept="application/json",
        contentType="application/json",
        body=request_body,
    )
    logger.info("Received response from Bedrock")

    # Extract response text
    response_body = json.loads(response.get("body").read())
    output_text = response_body.get("content")[0].get("text")
    logger.info("Output text: %s", output_text)

    return output_text


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
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
