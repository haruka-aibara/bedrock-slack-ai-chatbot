"""
tests/test_slack_ai_chatbot_lambda.py
Tests for the Slack-facing Lambda that receives app_mention events and enqueues
them onto SQS for the Bedrock backend.
"""

import json
import time
from types import ModuleType
from unittest import mock

import pytest

from conftest import FRONTEND_ENV


def mention_event(text: str, **overrides: str) -> dict[str, str]:
    """Build a minimal Slack ``app_mention`` event payload."""
    event = {
        "channel": "C0000000001",
        "ts": "1700000000.000100",
        "text": text,
        "user": "U0000000009",
    }
    event.update(overrides)
    return event


def slack_body(event_id: str = "Ev0000000001") -> dict[str, str]:
    """Build the outer Slack request payload, which is where event_id lives."""
    return {"type": "event_callback", "event_id": event_id, "team_id": "T0000000001"}


def sent_body(frontend: ModuleType) -> dict[str, str]:
    """Decode the JSON body of the single message pushed to SQS."""
    frontend.sqs.send_message.assert_called_once()
    return json.loads(frontend.sqs.send_message.call_args.kwargs["MessageBody"])


class TestHandleAppMentionEvents:
    def test_sends_message_to_the_configured_queue(self, frontend: ModuleType) -> None:
        frontend.handle_app_mention_events(mention_event("<@U0BOT> hello"), body=slack_body(), say=mock.Mock())

        assert frontend.sqs.send_message.call_args.kwargs["QueueUrl"] == FRONTEND_ENV["BACKEND_QUEUE_URL"]

    def test_forwards_channel_thread_and_text(self, frontend: ModuleType) -> None:
        event = mention_event("<@U0BOT> Pythonのリスト内包表記って何？")

        frontend.handle_app_mention_events(event, body=slack_body(), say=mock.Mock())

        assert sent_body(frontend) | {"enqueued_at": 0} == {
            "channel_id": "C0000000001",
            "thread_ts": "1700000000.000100",
            "message_ts": "1700000000.000100",
            "input_text": "Pythonのリスト内包表記って何？",
            "event_id": "Ev0000000001",
            "enqueued_at": 0,
        }

    def test_forwards_this_mentions_own_timestamp_separately_from_the_thread_root(
        self, frontend: ModuleType
    ) -> None:
        # The backend reads the thread for context and needs to know which message is
        # the question, so it can ignore anything posted after it.
        event = mention_event("<@U0BOT> どうおもう？", ts="1700000999.000900", thread_ts="1700000000.000100")

        frontend.handle_app_mention_events(event, body=slack_body(), say=mock.Mock())

        body = sent_body(frontend)
        assert body["thread_ts"] == "1700000000.000100"
        assert body["message_ts"] == "1700000999.000900"

    def test_forwards_the_event_id_so_the_backend_can_deduplicate(self, frontend: ModuleType) -> None:
        frontend.handle_app_mention_events(mention_event("<@U0BOT> hi"), body=slack_body("Ev999"), say=mock.Mock())

        assert sent_body(frontend)["event_id"] == "Ev999"

    def test_tolerates_a_payload_without_an_event_id(self, frontend: ModuleType) -> None:
        frontend.handle_app_mention_events(mention_event("<@U0BOT> hi"), body={}, say=mock.Mock())

        assert sent_body(frontend)["event_id"] == ""

    def test_stamps_the_enqueue_time_so_the_backend_can_judge_staleness(self, frontend: ModuleType) -> None:
        before = int(time.time())

        frontend.handle_app_mention_events(mention_event("<@U0BOT> hi"), body=slack_body(), say=mock.Mock())

        assert before <= sent_body(frontend)["enqueued_at"] <= int(time.time())

    def test_strips_the_mention_and_surrounding_whitespace(self, frontend: ModuleType) -> None:
        frontend.handle_app_mention_events(mention_event("  <@U0BOT>   質問です  "), body=slack_body(), say=mock.Mock())

        assert sent_body(frontend)["input_text"] == "質問です"

    def test_strips_every_mention_wherever_it_appears(self, frontend: ModuleType) -> None:
        frontend.handle_app_mention_events(
            mention_event("<@U0BOT> ask <@UABC123> about <@U0BOT> this"),
            body=slack_body(),
            say=mock.Mock(),
        )

        assert sent_body(frontend)["input_text"] == "ask  about  this"

    def test_keeps_text_that_merely_looks_like_a_mention(self, frontend: ModuleType) -> None:
        # Lowercase ids and channel links are not user mentions and must survive.
        frontend.handle_app_mention_events(mention_event("<@U0BOT> compare <#C123|general>"), body=slack_body(), say=mock.Mock())

        assert sent_body(frontend)["input_text"] == "compare <#C123|general>"

    def test_uses_the_message_timestamp_as_thread_root_for_a_top_level_mention(self, frontend: ModuleType) -> None:
        event = mention_event("<@U0BOT> hi", ts="1700000000.000100")

        frontend.handle_app_mention_events(event, body=slack_body(), say=mock.Mock())

        assert sent_body(frontend)["thread_ts"] == "1700000000.000100"

    def test_keeps_the_existing_thread_root_for_a_reply_inside_a_thread(self, frontend: ModuleType) -> None:
        event = mention_event("<@U0BOT> follow-up", ts="1700000999.000900", thread_ts="1700000000.000100")

        frontend.handle_app_mention_events(event, body=slack_body(), say=mock.Mock())

        # The reply must be attributed to the thread root, not to its own timestamp,
        # otherwise the backend starts a fresh conversation for every follow-up.
        assert sent_body(frontend)["thread_ts"] == "1700000000.000100"

    def test_forwards_a_mention_with_no_text_as_an_empty_string(self, frontend: ModuleType) -> None:
        # The backend is responsible for replying with the "empty input" message.
        frontend.handle_app_mention_events(mention_event("<@U0BOT>"), body=slack_body(), say=mock.Mock())

        assert sent_body(frontend)["input_text"] == ""

    def test_does_not_reply_directly_to_slack(self, frontend: ModuleType) -> None:
        say = mock.Mock()

        frontend.handle_app_mention_events(mention_event("<@U0BOT> hi"), body=slack_body(), say=say)

        # Replying here would race the backend and duplicate the answer.
        say.assert_not_called()


class TestLambdaHandler:
    def test_delegates_the_api_gateway_event_to_the_slack_request_handler(self, frontend: ModuleType) -> None:
        event = {"body": "payload", "headers": {}}
        context = object()

        with mock.patch.object(frontend, "SlackRequestHandler") as handler_cls:
            handler_cls.return_value.handle.return_value = {"statusCode": 200, "body": ""}

            result = frontend.lambda_handler(event, context)

        handler_cls.assert_called_once_with(app=frontend.app)
        handler_cls.return_value.handle.assert_called_once_with(event, context)
        assert result == {"statusCode": 200, "body": ""}

    @pytest.mark.parametrize("header", ["x-slack-retry-num", "X-Slack-Retry-Num"])
    def test_acknowledges_a_retried_delivery_without_running_the_listener(
        self, frontend: ModuleType, header: str
    ) -> None:
        # Slack's deadline is 3 seconds and a cold start alone can miss it. Running the
        # listener again would enqueue the same question and the bot would answer twice.
        event = {"body": "payload", "headers": {header: "1", "x-slack-retry-reason": "http_timeout"}}

        with mock.patch.object(frontend, "SlackRequestHandler") as handler_cls:
            result = frontend.lambda_handler(event, object())

        assert result["statusCode"] == 200
        handler_cls.assert_not_called()
        frontend.sqs.send_message.assert_not_called()

    def test_handles_a_request_that_carries_no_headers(self, frontend: ModuleType) -> None:
        with mock.patch.object(frontend, "SlackRequestHandler") as handler_cls:
            handler_cls.return_value.handle.return_value = {"statusCode": 200, "body": ""}

            frontend.lambda_handler({"body": "payload"}, object())

        handler_cls.return_value.handle.assert_called_once()


class TestConfiguration:
    def test_queue_url_defaults_to_empty_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Documents the current fallback: a missing queue URL surfaces as an SQS
        # error at send time rather than as an import-time failure.
        from conftest import FRONTEND_DIR, load_lambda_module

        for key, value in FRONTEND_ENV.items():
            monkeypatch.setenv(key, value)
        monkeypatch.delenv("BACKEND_QUEUE_URL")

        with mock.patch("boto3.client"), mock.patch("slack_bolt.App") as app_cls:
            app_cls.return_value.event.return_value = lambda handler: handler
            module = load_lambda_module("frontend_lambda_function_no_queue", FRONTEND_DIR)

        assert module.sqs_queue_url == ""
