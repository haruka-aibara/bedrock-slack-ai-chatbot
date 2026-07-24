"""
tests/conftest.py
Shared fixtures for loading the two Lambda handlers under test.

Both Lambda directories contain a module literally named ``lambda_function`` (and a
duplicated ``boto3_utils``), so they cannot both be imported by name in one pytest
session. Each handler is therefore loaded from its file path under a unique module
name, with its own directory placed on ``sys.path`` so the sibling ``boto3_utils``
import resolves against the right copy.

Both handlers also build their AWS/Slack clients at import time, so every client
factory is patched *before* the module body runs. Each test gets a freshly loaded
module, which keeps the injected mocks isolated between tests.
"""

import importlib.util
import json
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest import mock

import pytest
from botocore.exceptions import ClientError

# Importing the handlers must not litter the Lambda source directories with
# __pycache__ entries: those directories are zipped by `data.archive_file`, and stray
# files there change `source_code_hash` and cause spurious redeploys.
sys.dont_write_bytecode = True

REPO_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = REPO_ROOT / "lambda_function_slack_ai_chatbot"
BACKEND_DIR = REPO_ROOT / "lambda_function_bedrock_backend"

FRONTEND_ENV = {
    "SLACK_BOT_TOKEN": "xoxb-test-token",
    "SLACK_SIGNING_SECRET": "test-signing-secret",
    "BACKEND_QUEUE_URL": "https://sqs.ap-northeast-1.amazonaws.com/123456789012/test-queue",
}

BACKEND_ENV = {
    "SLACK_BOT_TOKEN": "xoxb-test-token",
    "BEDROCK_MODEL_ID": "arn:aws:bedrock:ap-northeast-1:123456789012:inference-profile/test",
    "BEDROCK_MAX_TOKENS": "1000",
    "DYNAMODB_TABLE_NAME": "test-conversation-history",
}


def load_lambda_module(module_name: str, directory: Path) -> ModuleType:
    """Execute ``<directory>/lambda_function.py`` as a module named ``module_name``."""
    sys.path.insert(0, str(directory))
    sys.modules.pop("boto3_utils", None)
    try:
        spec = importlib.util.spec_from_file_location(module_name, directory / "lambda_function.py")
        if spec is None or spec.loader is None:  # pragma: no cover - defensive
            raise ImportError(f"cannot load lambda_function.py from {directory}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(directory))
        sys.modules.pop("boto3_utils", None)
        sys.modules.pop(module_name, None)


def sqs_event(body: dict[str, Any]) -> dict[str, Any]:
    """Build the SQS-triggered Lambda event envelope around a message body."""
    return {"Records": [{"body": json.dumps(body)}]}


def bedrock_response(text: str) -> dict[str, Any]:
    """Build a ``converse`` response carrying ``text`` as its single content block."""
    return {
        "output": {"message": {"role": "assistant", "content": [{"text": text}]}},
        "usage": {"inputTokens": 10, "outputTokens": 20},
        "stopReason": "end_turn",
    }


def conditional_check_failed(operation: str) -> ClientError:
    """Build the error DynamoDB raises when a ConditionExpression is not satisfied."""
    return ClientError(
        {"Error": {"Code": "ConditionalCheckFailedException", "Message": "The conditional request failed"}},
        operation,
    )


@pytest.fixture
def frontend_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set the environment variables the Slack frontend Lambda reads at import time."""
    for key, value in FRONTEND_ENV.items():
        monkeypatch.setenv(key, value)


@pytest.fixture
def backend_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set the environment variables the Bedrock backend Lambda reads at import time."""
    for key, value in BACKEND_ENV.items():
        monkeypatch.setenv(key, value)


@pytest.fixture
def frontend(frontend_env: None) -> Iterator[ModuleType]:
    """The Slack frontend handler with a mocked SQS client and Slack Bolt app.

    ``slack_bolt.App`` is replaced so no token verification request is made at import,
    and its ``event`` decorator is made a pass-through so the registered handler stays
    a plain function the tests can call directly.
    """
    with mock.patch("boto3.client"), mock.patch("slack_bolt.App") as app_cls:
        app_cls.return_value.event.return_value = lambda handler: handler
        yield load_lambda_module("frontend_lambda_function", FRONTEND_DIR)


@pytest.fixture
def backend(backend_env: None) -> Iterator[ModuleType]:
    """The Bedrock backend handler with mocked Bedrock, DynamoDB and Slack clients."""
    with (
        mock.patch("boto3.client"),
        mock.patch("boto3.resource"),
        mock.patch("slack_sdk.WebClient"),
    ):
        yield load_lambda_module("backend_lambda_function", BACKEND_DIR)
