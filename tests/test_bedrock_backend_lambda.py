"""
tests/test_bedrock_backend_lambda.py
Tests for the SQS-triggered Lambda that keeps per-thread conversation history in
DynamoDB, calls Amazon Bedrock and replies in the Slack thread.
"""

import json
import time
from types import ModuleType
from unittest import mock

import pytest

from conftest import BACKEND_DIR, BACKEND_ENV, bedrock_response, load_lambda_module, sqs_event

THREAD_TS = "1700000000.000100"
CHANNEL_ID = "C0000000001"


def message_body(backend: ModuleType) -> dict[str, object]:
    """Decode the JSON request body handed to ``invoke_model``."""
    return json.loads(backend.bedrock_runtime.invoke_model.call_args.kwargs["body"])


def conversation(turns: int) -> list[dict[str, str]]:
    """Build ``turns`` alternating user/assistant messages, starting with the user."""
    messages = []
    for index in range(turns):
        role = "user" if index % 2 == 0 else "assistant"
        messages.append({"role": role, "content": f"message {index}"})
    return messages


class FakeConversationTable:
    """In-memory stand-in for the DynamoDB table that actually round-trips items."""

    def __init__(self) -> None:
        self.items: dict[str, dict[str, object]] = {}

    def get_item(self, Key: dict[str, str]) -> dict[str, object]:  # noqa: N803 - boto3 spelling
        item = self.items.get(Key["thread_ts"])
        return {"Item": item} if item is not None else {}

    def put_item(self, Item: dict[str, object]) -> None:  # noqa: N803 - boto3 spelling
        self.items[str(Item["thread_ts"])] = Item


class TestGetConversationHistory:
    def test_returns_an_empty_history_for_an_unknown_thread(self, backend: ModuleType) -> None:
        backend.conversation_table.get_item.return_value = {}

        assert backend.get_conversation_history(THREAD_TS) == []

    def test_returns_an_empty_history_when_the_item_has_no_messages(self, backend: ModuleType) -> None:
        backend.conversation_table.get_item.return_value = {"Item": {"thread_ts": THREAD_TS}}

        assert backend.get_conversation_history(THREAD_TS) == []

    def test_returns_the_stored_messages_in_order(self, backend: ModuleType) -> None:
        stored = conversation(4)
        backend.conversation_table.get_item.return_value = {"Item": {"thread_ts": THREAD_TS, "messages": stored}}

        assert backend.get_conversation_history(THREAD_TS) == stored

    def test_looks_the_item_up_by_thread_timestamp(self, backend: ModuleType) -> None:
        backend.conversation_table.get_item.return_value = {}

        backend.get_conversation_history(THREAD_TS)

        backend.conversation_table.get_item.assert_called_once_with(Key={"thread_ts": THREAD_TS})


class TestSaveConversationHistory:
    def test_stores_the_messages_under_the_thread_timestamp(self, backend: ModuleType) -> None:
        messages = conversation(2)

        backend.save_conversation_history(THREAD_TS, messages)

        item = backend.conversation_table.put_item.call_args.kwargs["Item"]
        assert item["thread_ts"] == THREAD_TS
        assert item["messages"] == messages

    def test_sets_a_seven_day_expiry(self, backend: ModuleType) -> None:
        assert backend.CONVERSATION_TTL_SECONDS == 7 * 24 * 60 * 60

        before = int(time.time())
        backend.save_conversation_history(THREAD_TS, conversation(2))
        after = int(time.time())

        expires_at = backend.conversation_table.put_item.call_args.kwargs["Item"]["expires_at"]
        assert before + backend.CONVERSATION_TTL_SECONDS <= expires_at <= after + backend.CONVERSATION_TTL_SECONDS

    def test_stores_the_expiry_as_an_integer_for_dynamodb_ttl(self, backend: ModuleType) -> None:
        # DynamoDB only expires items whose TTL attribute is a Number epoch second.
        backend.save_conversation_history(THREAD_TS, conversation(2))

        assert isinstance(backend.conversation_table.put_item.call_args.kwargs["Item"]["expires_at"], int)


class TestTrimHistory:
    def test_returns_a_short_history_untouched(self, backend: ModuleType) -> None:
        messages = conversation(4)

        assert backend.trim_history(messages) == messages

    def test_returns_a_history_at_the_limit_untouched(self, backend: ModuleType) -> None:
        messages = conversation(backend.MAX_HISTORY_MESSAGES)

        assert backend.trim_history(messages) == messages

    def test_keeps_the_newest_messages(self, backend: ModuleType) -> None:
        messages = conversation(30)

        trimmed = backend.trim_history(messages)

        assert trimmed[-1] == messages[-1]
        assert trimmed == messages[-len(trimmed) :]

    def test_never_exceeds_the_limit(self, backend: ModuleType) -> None:
        assert len(backend.trim_history(conversation(100))) <= backend.MAX_HISTORY_MESSAGES

    def test_drops_an_assistant_reply_left_at_the_head_of_the_window(self, backend: ModuleType) -> None:
        # One message over the limit, so the window opens on the assistant reply at
        # index 1. That orphaned reply is rejected by Bedrock and goes too, costing
        # one message off the limit.
        messages = conversation(backend.MAX_HISTORY_MESSAGES + 1)
        assert messages[1]["role"] == "assistant"

        trimmed = backend.trim_history(messages)

        assert trimmed[0] == messages[2]
        assert len(trimmed) == backend.MAX_HISTORY_MESSAGES - 1

    def test_keeps_a_window_that_already_opens_on_a_user_message(self, backend: ModuleType) -> None:
        messages = conversation(backend.MAX_HISTORY_MESSAGES + 2)
        assert messages[2]["role"] == "user"

        trimmed = backend.trim_history(messages)

        assert trimmed[0] == messages[2]
        assert len(trimmed) == backend.MAX_HISTORY_MESSAGES

    def test_handles_an_empty_history(self, backend: ModuleType) -> None:
        assert backend.trim_history([]) == []


class TestGenerateAnswer:
    def test_invokes_the_configured_model_with_json_content_types(self, backend: ModuleType) -> None:
        backend.bedrock_runtime.invoke_model.return_value = bedrock_response("answer")

        backend.generate_answer(conversation(1))

        kwargs = backend.bedrock_runtime.invoke_model.call_args.kwargs
        assert kwargs["modelId"] == BACKEND_ENV["BEDROCK_MODEL_ID"]
        assert kwargs["accept"] == "application/json"
        assert kwargs["contentType"] == "application/json"

    def test_sends_the_whole_conversation_with_the_anthropic_parameters(self, backend: ModuleType) -> None:
        backend.bedrock_runtime.invoke_model.return_value = bedrock_response("answer")
        messages = conversation(3)

        backend.generate_answer(messages)

        assert message_body(backend) == {
            "messages": messages,
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": int(BACKEND_ENV["BEDROCK_MAX_TOKENS"]),
        }

    def test_returns_the_text_of_the_first_content_block(self, backend: ModuleType) -> None:
        backend.bedrock_runtime.invoke_model.return_value = bedrock_response("リスト内包表記は…")

        assert backend.generate_answer(conversation(1)) == "リスト内包表記は…"

    def test_raises_when_bedrock_returns_no_body(self, backend: ModuleType) -> None:
        backend.bedrock_runtime.invoke_model.return_value = {"body": None}

        with pytest.raises(ValueError, match="Response body is None"):
            backend.generate_answer(conversation(1))


class TestLambdaHandlerRejectsEmptyInput:
    @pytest.mark.parametrize("input_text", ["", "   ", "\n\t "])
    def test_replies_with_an_error_and_returns_400(self, backend: ModuleType, input_text: str) -> None:
        event = sqs_event({"channel_id": CHANNEL_ID, "thread_ts": THREAD_TS, "input_text": input_text})

        result = backend.lambda_handler(event, None)

        assert result["statusCode"] == 400
        assert json.loads(result["body"]) == "Empty input text"
        kwargs = backend.slack_client.chat_postMessage.call_args.kwargs
        assert kwargs["channel"] == CHANNEL_ID
        assert kwargs["thread_ts"] == THREAD_TS
        assert kwargs["text"] == "入力テキストが空です。有効なテキストを入力してください。"

    def test_does_not_call_bedrock_or_touch_history(self, backend: ModuleType) -> None:
        event = sqs_event({"channel_id": CHANNEL_ID, "thread_ts": THREAD_TS, "input_text": "   "})

        backend.lambda_handler(event, None)

        backend.bedrock_runtime.invoke_model.assert_not_called()
        backend.conversation_table.put_item.assert_not_called()

    def test_rejects_a_missing_input_text_field(self, backend: ModuleType) -> None:
        event = sqs_event({"channel_id": CHANNEL_ID, "thread_ts": THREAD_TS})

        assert backend.lambda_handler(event, None)["statusCode"] == 400


class TestLambdaHandlerAnswersAMention:
    @pytest.fixture(autouse=True)
    def _bedrock_answers(self, backend: ModuleType) -> None:
        backend.conversation_table.get_item.return_value = {}
        backend.bedrock_runtime.invoke_model.return_value = bedrock_response("それは内包表記です")

    def test_returns_200(self, backend: ModuleType) -> None:
        event = sqs_event({"channel_id": CHANNEL_ID, "thread_ts": THREAD_TS, "input_text": "内包表記とは？"})

        result = backend.lambda_handler(event, None)

        assert result["statusCode"] == 200
        assert json.loads(result["body"]) == "Message sent successfully"

    def test_sends_the_new_user_message_to_bedrock(self, backend: ModuleType) -> None:
        event = sqs_event({"channel_id": CHANNEL_ID, "thread_ts": THREAD_TS, "input_text": "内包表記とは？"})

        backend.lambda_handler(event, None)

        assert message_body(backend)["messages"] == [{"role": "user", "content": "内包表記とは？"}]

    def test_replies_in_the_originating_thread(self, backend: ModuleType) -> None:
        event = sqs_event({"channel_id": CHANNEL_ID, "thread_ts": THREAD_TS, "input_text": "内包表記とは？"})

        backend.lambda_handler(event, None)

        backend.slack_client.chat_postMessage.assert_called_once_with(
            channel=CHANNEL_ID,
            thread_ts=THREAD_TS,
            text="それは内包表記です",
        )

    def test_persists_the_user_message_and_the_answer(self, backend: ModuleType) -> None:
        event = sqs_event({"channel_id": CHANNEL_ID, "thread_ts": THREAD_TS, "input_text": "内包表記とは？"})

        backend.lambda_handler(event, None)

        saved = backend.conversation_table.put_item.call_args.kwargs["Item"]["messages"]
        assert saved == [
            {"role": "user", "content": "内包表記とは？"},
            {"role": "assistant", "content": "それは内包表記です"},
        ]

    def test_continues_an_existing_thread_with_its_earlier_turns(self, backend: ModuleType) -> None:
        history = [
            {"role": "user", "content": "内包表記とは？"},
            {"role": "assistant", "content": "それは内包表記です"},
        ]
        backend.conversation_table.get_item.return_value = {"Item": {"messages": list(history)}}
        event = sqs_event({"channel_id": CHANNEL_ID, "thread_ts": THREAD_TS, "input_text": "じゃあ辞書版は？"})

        backend.lambda_handler(event, None)

        assert message_body(backend)["messages"] == [*history, {"role": "user", "content": "じゃあ辞書版は？"}]

    def test_loads_the_history_of_the_thread_the_message_belongs_to(self, backend: ModuleType) -> None:
        event = sqs_event({"channel_id": CHANNEL_ID, "thread_ts": THREAD_TS, "input_text": "hi"})

        backend.lambda_handler(event, None)

        backend.conversation_table.get_item.assert_called_once_with(Key={"thread_ts": THREAD_TS})


class TestLambdaHandlerTrimsLongThreads:
    @pytest.fixture(autouse=True)
    def _bedrock_answers(self, backend: ModuleType) -> None:
        backend.bedrock_runtime.invoke_model.return_value = bedrock_response("answer")

    def test_keeps_a_short_thread_intact(self, backend: ModuleType) -> None:
        history = conversation(backend.MAX_HISTORY_MESSAGES - 1)
        backend.conversation_table.get_item.return_value = {"Item": {"messages": history}}
        event = sqs_event({"channel_id": CHANNEL_ID, "thread_ts": THREAD_TS, "input_text": "next"})

        backend.lambda_handler(event, None)

        assert len(message_body(backend)["messages"]) == backend.MAX_HISTORY_MESSAGES

    def test_caps_a_long_thread_at_the_history_limit(self, backend: ModuleType) -> None:
        backend.conversation_table.get_item.return_value = {"Item": {"messages": conversation(40)}}
        event = sqs_event({"channel_id": CHANNEL_ID, "thread_ts": THREAD_TS, "input_text": "next"})

        backend.lambda_handler(event, None)

        assert len(message_body(backend)["messages"]) <= backend.MAX_HISTORY_MESSAGES

    def test_drops_the_oldest_turns_and_keeps_the_newest(self, backend: ModuleType) -> None:
        backend.conversation_table.get_item.return_value = {"Item": {"messages": conversation(40)}}
        event = sqs_event({"channel_id": CHANNEL_ID, "thread_ts": THREAD_TS, "input_text": "next"})

        backend.lambda_handler(event, None)

        sent = message_body(backend)["messages"]
        assert sent[0]["content"] != "message 0"
        assert sent[-1] == {"role": "user", "content": "next"}
        assert sent[-2] == {"role": "assistant", "content": "message 39"}

    @pytest.mark.parametrize("stored_turns", range(1, 42))
    def test_always_sends_a_conversation_that_starts_with_the_user(
        self, backend: ModuleType, stored_turns: int
    ) -> None:
        # Bedrock's Anthropic Messages API rejects a conversation whose first message
        # is from the assistant, so trimming must never cut a user turn loose from
        # the assistant reply that follows it.
        backend.conversation_table.get_item.return_value = {"Item": {"messages": conversation(stored_turns)}}
        event = sqs_event({"channel_id": CHANNEL_ID, "thread_ts": THREAD_TS, "input_text": "next"})

        backend.lambda_handler(event, None)

        assert message_body(backend)["messages"][0]["role"] == "user"

    def test_a_long_running_thread_stays_a_valid_conversation(self, backend: ModuleType) -> None:
        # Drive many real turns through the handler against a persistent fake table, so
        # trimming is exercised on the histories the handler itself writes back rather
        # than on synthetic ones. Every request must remain a well-formed conversation.
        table = FakeConversationTable()
        backend.conversation_table = table

        for turn in range(40):
            backend.bedrock_runtime.invoke_model.return_value = bedrock_response(f"answer {turn}")
            event = sqs_event({"channel_id": CHANNEL_ID, "thread_ts": THREAD_TS, "input_text": f"question {turn}"})

            backend.lambda_handler(event, None)

            sent = message_body(backend)["messages"]
            roles = [message["role"] for message in sent]
            assert len(sent) <= backend.MAX_HISTORY_MESSAGES
            assert roles[0] == "user", f"turn {turn} started the conversation with {roles[0]}"
            assert sent[-1] == {"role": "user", "content": f"question {turn}"}
            assert all(before != after for before, after in zip(roles, roles[1:], strict=False))


class TestImportTimeConfiguration:
    def _load_without(self, monkeypatch: pytest.MonkeyPatch, missing: str, name: str) -> ModuleType:
        for key, value in BACKEND_ENV.items():
            monkeypatch.setenv(key, value)
        monkeypatch.delenv(missing)
        with (
            mock.patch("boto3.client"),
            mock.patch("boto3.resource"),
            mock.patch("slack_sdk.WebClient"),
        ):
            return load_lambda_module(name, BACKEND_DIR)

    def test_refuses_to_start_without_a_model_id(self, monkeypatch: pytest.MonkeyPatch) -> None:
        with pytest.raises(ValueError, match="BEDROCK_MODEL_ID"):
            self._load_without(monkeypatch, "BEDROCK_MODEL_ID", "backend_no_model")

    def test_refuses_to_start_without_a_table_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        with pytest.raises(ValueError, match="DYNAMODB_TABLE_NAME"):
            self._load_without(monkeypatch, "DYNAMODB_TABLE_NAME", "backend_no_table")

    def test_falls_back_to_a_default_token_budget(self, monkeypatch: pytest.MonkeyPatch) -> None:
        module = self._load_without(monkeypatch, "BEDROCK_MAX_TOKENS", "backend_default_tokens")

        assert module.MAX_TOKENS == 1000
