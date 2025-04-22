"""
lambda_function_slack_ai_chatbot/lambda_function.py
This module handles Slack events and forwards requests to SQS for processing.
"""

import json
import logging
import os
import re

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
def handle_app_mention_events(event, say):
    """
    Handler for when the Slack app is mentioned.
    Extracts the message text and sends it to SQS for processing.
    
    Args:
        event (dict): The Slack event data
        say (callable): Function to send a message to Slack
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


def lambda_handler(event, context):
    """
    AWS Lambda function handler to process API Gateway events.
    
    Args:
        event (dict): AWS Lambda event data
        context (object): AWS Lambda context
        
    Returns:
        dict: Response from Slack handler
    """
    slack_handler = SlackRequestHandler(app=app)
    return slack_handler.handle(event, context)
