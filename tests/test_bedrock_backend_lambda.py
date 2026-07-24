"""
tests/test_bedrock_backend_lambda.py
Tests for the SQS-triggered Lambda that keeps per-thread conversation history in
DynamoDB, calls Amazon Bedrock and replies in the Slack thread.
"""

import time
from types import ModuleType
from typing import Any
from unittest import mock

import pytest

from conftest import (
    BACKEND_DIR,
    BACKEND_ENV,
    bedrock_response,
    conditional_check_failed,
    load_lambda_module,
    sqs_event,
)

THREAD_TS = "1700000000.000100"
CHANNEL_ID = "C0000000001"
EVENT_ID = "Ev0000000001"


def conversation(turns: int) -> list[dict[str, str]]:
    """Build ``turns`` alternating user/assistant messages, starting with the user."""
    messages = []
    for index in range(turns):
        role = "user" if index % 2 == 0 else "assistant"
        messages.append({"role": role, "content": f"message {index}"})
    return messages


def sent_conversation(backend: ModuleType) -> list[dict[str, str]]:
    """Flatten the Converse ``messages`` argument back into role/content pairs."""
    messages = backend.bedrock_runtime.converse.call_args.kwargs["messages"]
    return [
        {"role": message["role"], "content": "".join(block["text"] for block in message["content"])}
        for message in messages
    ]


def mention(input_text: str = "内包表記とは？", event_id: str = EVENT_ID) -> dict[str, Any]:
    """Build the SQS event the frontend Lambda enqueues for a mention."""
    return sqs_event(
        {
            "channel_id": CHANNEL_ID,
            "thread_ts": THREAD_TS,
            "input_text": input_text,
            "event_id": event_id,
        }
    )


class FakeConversationTable:
    """In-memory stand-in for the DynamoDB table, including its conditional writes."""

    def __init__(self) -> None:
        self.items: dict[str, dict[str, Any]] = {}

    def get_item(self, Key: dict[str, str]) -> dict[str, Any]:  # noqa: N803 - boto3 spelling
        item = self.items.get(Key["thread_ts"])
        return {"Item": item} if item is not None else {}

    def put_item(
        self,
        Item: dict[str, Any],  # noqa: N803 - boto3 spelling
        ConditionExpression: str | None = None,  # noqa: N803
        ExpressionAttributeNames: dict[str, str] | None = None,  # noqa: N803, ARG002
        ExpressionAttributeValues: dict[str, Any] | None = None,  # noqa: N803
    ) -> None:
        key = str(Item["thread_ts"])
        existing = self.items.get(key)
        values = ExpressionAttributeValues or {}
        if ConditionExpression and existing is not None:
            if "claim_expires_at" in ConditionExpression:
                if existing.get("claim_expires_at", 0) >= values[":now"]:
                    raise conditional_check_failed("PutItem")
            elif "#version" in ConditionExpression and existing.get("version", 0) != values[":expected"]:
                raise conditional_check_failed("PutItem")
        self.items[key] = Item

    def delete_item(self, Key: dict[str, str]) -> None:  # noqa: N803 - boto3 spelling
        self.items.pop(Key["thread_ts"], None)


class TestGetConversationHistory:
    def test_returns_an_empty_history_for_an_unknown_thread(self, backend: ModuleType) -> None:
        backend.conversation_table.get_item.return_value = {}

        assert backend.get_conversation_history(THREAD_TS) == ([], 0)

    def test_returns_an_empty_history_when_the_item_has_no_messages(self, backend: ModuleType) -> None:
        backend.conversation_table.get_item.return_value = {"Item": {"thread_ts": THREAD_TS}}

        assert backend.get_conversation_history(THREAD_TS).messages == []

    def test_returns_the_stored_messages_in_order(self, backend: ModuleType) -> None:
        stored = conversation(4)
        backend.conversation_table.get_item.return_value = {"Item": {"thread_ts": THREAD_TS, "messages": stored}}

        assert backend.get_conversation_history(THREAD_TS).messages == stored

    def test_reports_the_version_the_history_was_read_at(self, backend: ModuleType) -> None:
        backend.conversation_table.get_item.return_value = {"Item": {"messages": conversation(2), "version": 7}}

        assert backend.get_conversation_history(THREAD_TS).version == 7

    def test_treats_an_item_written_before_versioning_as_version_zero(self, backend: ModuleType) -> None:
        backend.conversation_table.get_item.return_value = {"Item": {"messages": conversation(2)}}

        assert backend.get_conversation_history(THREAD_TS).version == 0

    def test_looks_the_item_up_by_thread_timestamp(self, backend: ModuleType) -> None:
        backend.conversation_table.get_item.return_value = {}

        backend.get_conversation_history(THREAD_TS)

        backend.conversation_table.get_item.assert_called_once_with(Key={"thread_ts": THREAD_TS})


class TestSaveConversationHistory:
    def test_stores_the_messages_under_the_thread_timestamp(self, backend: ModuleType) -> None:
        messages = conversation(2)

        backend.save_conversation_history(THREAD_TS, messages, 0)

        item = backend.conversation_table.put_item.call_args.kwargs["Item"]
        assert item["thread_ts"] == THREAD_TS
        assert item["messages"] == messages

    def test_advances_the_version(self, backend: ModuleType) -> None:
        backend.save_conversation_history(THREAD_TS, conversation(2), 4)

        assert backend.conversation_table.put_item.call_args.kwargs["Item"]["version"] == 5

    def test_writes_only_if_nobody_else_advanced_the_version(self, backend: ModuleType) -> None:
        backend.save_conversation_history(THREAD_TS, conversation(2), 4)

        kwargs = backend.conversation_table.put_item.call_args.kwargs
        assert kwargs["ExpressionAttributeValues"][":expected"] == 4
        assert "attribute_not_exists(thread_ts)" in kwargs["ConditionExpression"]

    def test_sets_a_seven_day_expiry(self, backend: ModuleType) -> None:
        assert backend.CONVERSATION_TTL_SECONDS == 7 * 24 * 60 * 60

        before = int(time.time())
        backend.save_conversation_history(THREAD_TS, conversation(2), 0)
        after = int(time.time())

        expires_at = backend.conversation_table.put_item.call_args.kwargs["Item"]["expires_at"]
        assert before + backend.CONVERSATION_TTL_SECONDS <= expires_at <= after + backend.CONVERSATION_TTL_SECONDS

    def test_stores_the_expiry_as_an_integer_for_dynamodb_ttl(self, backend: ModuleType) -> None:
        # DynamoDB only expires items whose TTL attribute is a Number epoch second.
        backend.save_conversation_history(THREAD_TS, conversation(2), 0)

        assert isinstance(backend.conversation_table.put_item.call_args.kwargs["Item"]["expires_at"], int)


class TestEventClaims:
    def test_claims_an_event_nobody_holds(self, backend: ModuleType) -> None:
        assert backend.claim_event(EVENT_ID) is True

    def test_stores_the_claim_under_a_prefixed_key(self, backend: ModuleType) -> None:
        # Slack thread timestamps are numeric, so the prefix cannot collide with a
        # real conversation item sharing the table.
        backend.claim_event(EVENT_ID)

        key = backend.conversation_table.put_item.call_args.kwargs["Item"]["thread_ts"]
        assert key == f"{backend.CLAIM_KEY_PREFIX}{EVENT_ID}"
        assert not key[0].isdigit()

    def test_refuses_an_event_another_invocation_already_claimed(self, backend: ModuleType) -> None:
        backend.conversation_table.put_item.side_effect = conditional_check_failed("PutItem")

        assert backend.claim_event(EVENT_ID) is False

    def test_lets_a_claim_be_retaken_once_it_has_expired(self, backend: ModuleType) -> None:
        # The condition only rejects a claim that is still live, so an invocation
        # killed mid-flight cannot swallow the question forever.
        backend.claim_event(EVENT_ID)

        kwargs = backend.conversation_table.put_item.call_args.kwargs
        assert "claim_expires_at < :now" in kwargs["ConditionExpression"]
        assert kwargs["ExpressionAttributeValues"][":now"] <= int(time.time())

    def test_claim_expiry_matches_the_queue_visibility_timeout(self, backend: ModuleType) -> None:
        # A claim outliving the visibility timeout would block the very redelivery it
        # is supposed to let through.
        assert backend.CLAIM_TTL_SECONDS == 180

        before = int(time.time())
        backend.claim_event(EVENT_ID)

        expires = backend.conversation_table.put_item.call_args.kwargs["Item"]["claim_expires_at"]
        assert expires >= before + backend.CLAIM_TTL_SECONDS

    def test_propagates_errors_that_are_not_a_failed_condition(self, backend: ModuleType) -> None:
        backend.conversation_table.put_item.side_effect = RuntimeError("boom")

        with pytest.raises(RuntimeError):
            backend.claim_event(EVENT_ID)

    def test_releasing_a_claim_deletes_it(self, backend: ModuleType) -> None:
        backend.release_event_claim(EVENT_ID)

        backend.conversation_table.delete_item.assert_called_once_with(
            Key={"thread_ts": f"{backend.CLAIM_KEY_PREFIX}{EVENT_ID}"}
        )


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

    def test_never_exceeds_the_message_limit(self, backend: ModuleType) -> None:
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

    def test_drops_old_turns_once_the_size_budget_is_exceeded(self, backend: ModuleType) -> None:
        # Well under the message cap, so only the size cap can be doing the trimming.
        big = "x" * (backend.MAX_HISTORY_CHARACTERS // 4)
        messages = [{"role": "user" if index % 2 == 0 else "assistant", "content": big} for index in range(8)]

        trimmed = backend.trim_history(messages)

        assert len(trimmed) < len(messages)
        assert sum(len(message["content"]) for message in trimmed) <= backend.MAX_HISTORY_CHARACTERS

    def test_keeps_the_newest_message_even_when_it_alone_blows_the_budget(self, backend: ModuleType) -> None:
        # Dropping it would leave nothing to answer, so the cap yields here.
        messages = [{"role": "user", "content": "x" * (backend.MAX_HISTORY_CHARACTERS * 2)}]

        assert backend.trim_history(messages) == messages

    def test_size_trimming_still_leaves_the_user_at_the_head(self, backend: ModuleType) -> None:
        big = "x" * (backend.MAX_HISTORY_CHARACTERS // 3)
        messages = [{"role": "user" if index % 2 == 0 else "assistant", "content": big} for index in range(6)]

        assert backend.trim_history(messages)[0]["role"] == "user"


class TestGenerateAnswer:
    def test_calls_converse_on_the_configured_model(self, backend: ModuleType) -> None:
        backend.bedrock_runtime.converse.return_value = bedrock_response("answer")

        backend.generate_answer(conversation(1))

        assert backend.bedrock_runtime.converse.call_args.kwargs["modelId"] == BACKEND_ENV["BEDROCK_MODEL_ID"]

    def test_sends_the_whole_conversation_in_converse_shape(self, backend: ModuleType) -> None:
        backend.bedrock_runtime.converse.return_value = bedrock_response("answer")

        backend.generate_answer(conversation(3))

        assert backend.bedrock_runtime.converse.call_args.kwargs["messages"] == [
            {"role": "user", "content": [{"text": "message 0"}]},
            {"role": "assistant", "content": [{"text": "message 1"}]},
            {"role": "user", "content": [{"text": "message 2"}]},
        ]

    def test_passes_the_token_budget_through_inference_config(self, backend: ModuleType) -> None:
        backend.bedrock_runtime.converse.return_value = bedrock_response("answer")

        backend.generate_answer(conversation(1))

        kwargs = backend.bedrock_runtime.converse.call_args.kwargs
        assert kwargs["inferenceConfig"] == {"maxTokens": int(BACKEND_ENV["BEDROCK_MAX_TOKENS"])}

    def test_returns_the_generated_text(self, backend: ModuleType) -> None:
        backend.bedrock_runtime.converse.return_value = bedrock_response("リスト内包表記は…")

        assert backend.generate_answer(conversation(1)) == "リスト内包表記は…"

    def test_joins_a_reply_split_across_content_blocks(self, backend: ModuleType) -> None:
        backend.bedrock_runtime.converse.return_value = {
            "output": {"message": {"role": "assistant", "content": [{"text": "前半"}, {"text": "後半"}]}}
        }

        assert backend.generate_answer(conversation(1)) == "前半後半"

    def test_raises_when_the_reply_carries_no_text(self, backend: ModuleType) -> None:
        backend.bedrock_runtime.converse.return_value = {
            "output": {"message": {"role": "assistant", "content": []}}
        }

        with pytest.raises(ValueError, match="no text content"):
            backend.generate_answer(conversation(1))


class TestLambdaHandlerRejectsEmptyInput:
    @pytest.mark.parametrize("input_text", ["", "   ", "\n\t "])
    def test_replies_with_an_error_and_returns_400(self, backend: ModuleType, input_text: str) -> None:
        result = backend.lambda_handler(mention(input_text), None)

        assert result["statusCode"] == 400
        kwargs = backend.slack_client.chat_postMessage.call_args.kwargs
        assert kwargs["channel"] == CHANNEL_ID
        assert kwargs["thread_ts"] == THREAD_TS
        assert kwargs["text"] == "入力テキストが空です。有効なテキストを入力してください。"

    def test_does_not_call_bedrock_or_touch_history(self, backend: ModuleType) -> None:
        backend.lambda_handler(mention("   "), None)

        backend.bedrock_runtime.converse.assert_not_called()
        backend.conversation_table.put_item.assert_not_called()

    def test_rejects_a_missing_input_text_field(self, backend: ModuleType) -> None:
        event = sqs_event({"channel_id": CHANNEL_ID, "thread_ts": THREAD_TS})

        assert backend.lambda_handler(event, None)["statusCode"] == 400


class TestLambdaHandlerAnswersAMention:
    @pytest.fixture(autouse=True)
    def _bedrock_answers(self, backend: ModuleType) -> None:
        backend.conversation_table.get_item.return_value = {}
        backend.bedrock_runtime.converse.return_value = bedrock_response("それは内包表記です")

    def test_returns_200(self, backend: ModuleType) -> None:
        assert backend.lambda_handler(mention(), None)["statusCode"] == 200

    def test_sends_the_new_user_message_to_bedrock(self, backend: ModuleType) -> None:
        backend.lambda_handler(mention(), None)

        assert sent_conversation(backend) == [{"role": "user", "content": "内包表記とは？"}]

    def test_replies_in_the_originating_thread(self, backend: ModuleType) -> None:
        backend.lambda_handler(mention(), None)

        backend.slack_client.chat_postMessage.assert_called_once_with(
            channel=CHANNEL_ID,
            thread_ts=THREAD_TS,
            text="それは内包表記です",
        )

    def test_persists_the_user_message_and_the_answer(self, backend: ModuleType) -> None:
        backend.lambda_handler(mention(), None)

        saved = [
            call.kwargs["Item"]
            for call in backend.conversation_table.put_item.call_args_list
            if call.kwargs["Item"]["thread_ts"] == THREAD_TS
        ][-1]
        assert saved["messages"] == [
            {"role": "user", "content": "内包表記とは？"},
            {"role": "assistant", "content": "それは内包表記です"},
        ]

    def test_continues_an_existing_thread_with_its_earlier_turns(self, backend: ModuleType) -> None:
        history = [
            {"role": "user", "content": "内包表記とは？"},
            {"role": "assistant", "content": "それは内包表記です"},
        ]
        backend.conversation_table.get_item.return_value = {"Item": {"messages": list(history)}}

        backend.lambda_handler(mention("じゃあ辞書版は？"), None)

        assert sent_conversation(backend) == [*history, {"role": "user", "content": "じゃあ辞書版は？"}]

    def test_posts_to_slack_before_recording_the_turn(self, backend: ModuleType) -> None:
        # Saving first means a failed post retries against a history that already holds
        # the answer, and the question gets appended to the thread a second time.
        order = []
        backend.slack_client.chat_postMessage.side_effect = lambda **_: order.append("post")
        backend.conversation_table.put_item.side_effect = lambda **kwargs: order.append(
            "save" if kwargs["Item"]["thread_ts"] == THREAD_TS else "claim"
        )

        backend.lambda_handler(mention(), None)

        assert order == ["claim", "post", "save"]


class TestLambdaHandlerDeduplicates:
    @pytest.fixture(autouse=True)
    def _bedrock_answers(self, backend: ModuleType) -> None:
        backend.conversation_table.get_item.return_value = {}
        backend.bedrock_runtime.converse.return_value = bedrock_response("answer")

    def test_claims_the_event_before_doing_any_work(self, backend: ModuleType) -> None:
        backend.lambda_handler(mention(), None)

        claim_key = backend.conversation_table.put_item.call_args_list[0].kwargs["Item"]["thread_ts"]
        assert claim_key == f"{backend.CLAIM_KEY_PREFIX}{EVENT_ID}"

    def test_skips_an_event_another_invocation_already_claimed(self, backend: ModuleType) -> None:
        backend.conversation_table.put_item.side_effect = conditional_check_failed("PutItem")

        result = backend.lambda_handler(mention(), None)

        assert result["statusCode"] == 200
        backend.bedrock_runtime.converse.assert_not_called()
        backend.slack_client.chat_postMessage.assert_not_called()

    def test_a_redelivered_message_is_answered_only_once(self, backend: ModuleType) -> None:
        backend.conversation_table = FakeConversationTable()

        backend.lambda_handler(mention(), None)
        backend.lambda_handler(mention(), None)

        assert backend.slack_client.chat_postMessage.call_count == 1

    def test_a_different_question_in_the_same_thread_is_still_answered(self, backend: ModuleType) -> None:
        backend.conversation_table = FakeConversationTable()

        backend.lambda_handler(mention("one", event_id="Ev1"), None)
        backend.lambda_handler(mention("two", event_id="Ev2"), None)

        assert backend.slack_client.chat_postMessage.call_count == 2

    def test_releases_the_claim_when_bedrock_fails_so_sqs_can_retry(self, backend: ModuleType) -> None:
        table = FakeConversationTable()
        backend.conversation_table = table
        backend.bedrock_runtime.converse.side_effect = RuntimeError("ThrottlingException")

        with pytest.raises(RuntimeError):
            backend.lambda_handler(mention(), None)

        assert f"{backend.CLAIM_KEY_PREFIX}{EVENT_ID}" not in table.items

    def test_the_retry_after_a_failure_goes_through(self, backend: ModuleType) -> None:
        backend.conversation_table = FakeConversationTable()
        backend.bedrock_runtime.converse.side_effect = [RuntimeError("throttled"), bedrock_response("answer")]

        with pytest.raises(RuntimeError):
            backend.lambda_handler(mention(), None)
        result = backend.lambda_handler(mention(), None)

        assert result["statusCode"] == 200
        assert backend.slack_client.chat_postMessage.call_count == 1

    def test_releases_the_claim_when_the_slack_post_fails(self, backend: ModuleType) -> None:
        table = FakeConversationTable()
        backend.conversation_table = table
        backend.slack_client.chat_postMessage.side_effect = RuntimeError("slack down")

        with pytest.raises(RuntimeError):
            backend.lambda_handler(mention(), None)

        assert f"{backend.CLAIM_KEY_PREFIX}{EVENT_ID}" not in table.items
        assert THREAD_TS not in table.items

    def test_still_works_for_a_message_without_an_event_id(self, backend: ModuleType) -> None:
        # Messages enqueued before event_id was forwarded must not be dropped.
        event = sqs_event({"channel_id": CHANNEL_ID, "thread_ts": THREAD_TS, "input_text": "hi"})

        assert backend.lambda_handler(event, None)["statusCode"] == 200
        backend.slack_client.chat_postMessage.assert_called_once()


class TestLambdaHandlerHandlesConcurrentTurns:
    def test_a_losing_racer_fails_so_sqs_retries_against_fresh_history(self, backend: ModuleType) -> None:
        # Two mentions in one thread read the same history; letting both write would
        # erase one turn, so the second write is rejected and the message is retried.
        table = FakeConversationTable()
        table.items[THREAD_TS] = {"thread_ts": THREAD_TS, "messages": conversation(2), "version": 3}
        backend.conversation_table = table
        backend.bedrock_runtime.converse.return_value = bedrock_response("answer")

        stale_version = backend.ConversationState(messages=conversation(2), version=2)
        with mock.patch.object(backend, "get_conversation_history", return_value=stale_version):
            with pytest.raises(Exception, match="ConditionalCheckFailed"):
                backend.lambda_handler(mention(), None)

        assert table.items[THREAD_TS]["version"] == 3


class TestLambdaHandlerTrimsLongThreads:
    @pytest.fixture(autouse=True)
    def _bedrock_answers(self, backend: ModuleType) -> None:
        backend.bedrock_runtime.converse.return_value = bedrock_response("answer")

    def test_caps_a_long_thread_at_the_history_limit(self, backend: ModuleType) -> None:
        backend.conversation_table.get_item.return_value = {"Item": {"messages": conversation(40)}}

        backend.lambda_handler(mention("next"), None)

        assert len(sent_conversation(backend)) <= backend.MAX_HISTORY_MESSAGES

    def test_drops_the_oldest_turns_and_keeps_the_newest(self, backend: ModuleType) -> None:
        backend.conversation_table.get_item.return_value = {"Item": {"messages": conversation(40)}}

        backend.lambda_handler(mention("next"), None)

        sent = sent_conversation(backend)
        assert sent[0]["content"] != "message 0"
        assert sent[-1] == {"role": "user", "content": "next"}
        assert sent[-2] == {"role": "assistant", "content": "message 39"}

    @pytest.mark.parametrize("stored_turns", range(1, 42))
    def test_always_sends_a_conversation_that_starts_with_the_user(
        self, backend: ModuleType, stored_turns: int
    ) -> None:
        # Bedrock rejects a conversation whose first message is from the assistant, so
        # trimming must never cut a user turn loose from the reply that follows it.
        backend.conversation_table.get_item.return_value = {"Item": {"messages": conversation(stored_turns)}}

        backend.lambda_handler(mention("next"), None)

        assert sent_conversation(backend)[0]["role"] == "user"

    def test_a_long_running_thread_stays_a_valid_conversation(self, backend: ModuleType) -> None:
        # Drive many real turns through the handler against a persistent fake table, so
        # trimming is exercised on the histories the handler itself writes back rather
        # than on synthetic ones. Every request must remain a well-formed conversation.
        backend.conversation_table = FakeConversationTable()

        for turn in range(40):
            backend.bedrock_runtime.converse.return_value = bedrock_response(f"answer {turn}")

            backend.lambda_handler(mention(f"question {turn}", event_id=f"Ev{turn}"), None)

            sent = sent_conversation(backend)
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
