"""
lambda_function_slack_ai_chatbot/lambda_function.py
This module handles Slack events and forwards requests to SQS for processing.
"""

import json
import logging
import os
import re
from typing import Any, Callable, Dict

import boto3
from slack_bolt import App
from slack_bolt.adapter.aws_lambda import SlackRequestHandler

# Configure logger
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize AWS SQS client
sqs = boto3.client("sqs")

# Get SQS queue URL from environment variable
sqs_queue_url = os.environ.get("BACKEND_QUEUE_URL")

# Initialize Slack Bolt app
app = App(
    token=os.environ.get("SLACK_BOT_TOKEN"),
    signing_secret=os.environ.get("SLACK_SIGNING_SECRET"),
    process_before_response=True,
)


@app.event("app_mention")
def handle_app_mention_events(event: Dict[str, Any], say: Callable) -> None:
    """
    Handler for when the Slack app is mentioned.
    Extracts the message text and sends it to SQS for processing.

    Args:
        event: The Slack event data containing message information
        say: Function to send a message to Slack
    """
    # Get channel ID from event
    channel_id = event["channel"]

    # Remove the app mention pattern from text and strip whitespace
    input_text = re.sub(r"<@[A-Z0-9]+>", "", event["text"]).strip()

    # Send message to SQS for processing
    sqs.send_message(
        QueueUrl=sqs_queue_url,
        MessageBody=json.dumps(
            {
                "channel_id": channel_id,
                "input_text": input_text,
            }
        ),
    )


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    AWS Lambda function handler to process API Gateway events.

    Args:
        event: AWS Lambda event data from API Gateway
        context: AWS Lambda context object

    Returns:
        Response from Slack handler with appropriate status code and body
    """
    slack_handler = SlackRequestHandler(app=app)
    return slack_handler.handle(event, context)
