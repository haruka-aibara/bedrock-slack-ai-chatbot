"""
boto3_utils.py
This module provides centralized boto3 client instantiation for the application.
"""

import logging
from typing import TYPE_CHECKING

import boto3

# Configure logger
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Type annotation for boto3 clients
if TYPE_CHECKING:
    from mypy_boto3_bedrock_runtime import BedrockRuntimeClient
    from mypy_boto3_sqs import SQSClient

    def get_bedrock_runtime_client() -> BedrockRuntimeClient:
        """Get a properly typed BedrockRuntime client"""
        return boto3.client("bedrock-runtime")

    def get_sqs_client() -> SQSClient:
        """Get a properly typed SQS client"""
        return boto3.client("sqs")
else:

    def get_bedrock_runtime_client():
        """Get a BedrockRuntime client"""
        return boto3.client("bedrock-runtime")

    def get_sqs_client():
        """Get an SQS client"""
        return boto3.client("sqs")
