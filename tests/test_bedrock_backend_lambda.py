"""
tests/test_bedrock_backend_lambda.py
Tests for the SQS-triggered Lambda that reads the Slack thread for context, calls
Amazon Bedrock and replies in that thread.
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
MESSAGE_TS = "1700000000.000900"
CHANNEL_ID = "C0000000001"
EVENT_ID = "Ev0000000001"


def human(text: str, ts: str = "1700000000.000200") -> dict[str, Any]:
    """Build a Slack thread message posted by a person."""
    return {"type": "message", "user": "U0000000009", "text": text, "ts": ts}


def bot(text: str, ts: str = "1700000000.000300") -> dict[str, Any]:
    """Build a Slack thread message posted by this app."""
    return {"type": "message", "bot_id": "B0000000001", "text": text, "ts": ts}


def set_thread(backend: ModuleType, *messages: dict[str, Any], has_more: bool = False) -> None:
    """Make ``conversations_replies`` return this thread."""
    backend.slack_client.conversations_replies.return_value = {
        "messages": list(messages),
        "has_more": has_more,
    }


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


def mention(
    input_text: str = "どうおもう？",
    event_id: str = EVENT_ID,
    age_seconds: int = 0,
    message_ts: str = MESSAGE_TS,
) -> dict[str, Any]:
    """Build the SQS event the frontend Lambda enqueues for a mention."""
    return sqs_event(
        {
            "channel_id": CHANNEL_ID,
            "thread_ts": THREAD_TS,
            "message_ts": message_ts,
            "input_text": input_text,
            "event_id": event_id,
            "enqueued_at": int(time.time()) - age_seconds,
        }
    )


class FakeIdempotencyTable:
    """In-memory stand-in for the DynamoDB table, including its conditional write."""

    def __init__(self) -> None:
        self.items: dict[str, dict[str, Any]] = {}

    def put_item(
        self,
        Item: dict[str, Any],  # noqa: N803 - boto3 spelling
        ConditionExpression: str | None = None,  # noqa: N803
        ExpressionAttributeValues: dict[str, Any] | None = None,  # noqa: N803
    ) -> None:
        key = str(Item["event_id"])
        existing = self.items.get(key)
        values = ExpressionAttributeValues or {}
        if ConditionExpression and existing is not None and existing.get("claim_expires_at", 0) >= values[":now"]:
            raise conditional_check_failed("PutItem")
        self.items[key] = Item

    def delete_item(self, Key: dict[str, str]) -> None:  # noqa: N803 - boto3 spelling
        self.items.pop(Key["event_id"], None)


class TestFetchThreadMessages:
    def test_reads_the_thread_the_mention_belongs_to(self, backend: ModuleType) -> None:
        set_thread(backend, human("hello"))

        backend.fetch_thread_messages(CHANNEL_ID, THREAD_TS)

        kwargs = backend.slack_client.conversations_replies.call_args.kwargs
        assert kwargs["channel"] == CHANNEL_ID
        assert kwargs["ts"] == THREAD_TS

    def test_returns_the_messages_oldest_first(self, backend: ModuleType) -> None:
        set_thread(backend, human("first", ts="1.1"), human("second", ts="1.2"))

        texts = [message["text"] for message in backend.fetch_thread_messages(CHANNEL_ID, THREAD_TS)]

        assert texts == ["first", "second"]

    def test_ignores_messages_posted_after_the_question(self, backend: ModuleType) -> None:
        # While the question sat in the queue the thread may have moved on; answering
        # with context the asker had not written yet is confusing.
        set_thread(backend, human("before", ts="1.0"), human("the question", ts="2.0"), human("after", ts="3.0"))

        texts = [m["text"] for m in backend.fetch_thread_messages(CHANNEL_ID, THREAD_TS, latest_ts="2.0")]

        assert texts == ["before", "the question"]

    def test_pages_through_a_long_thread(self, backend: ModuleType) -> None:
        backend.slack_client.conversations_replies.side_effect = [
            {"messages": [human("page one")], "has_more": True, "response_metadata": {"next_cursor": "c1"}},
            {"messages": [human("page two")], "has_more": False},
        ]

        texts = [message["text"] for message in backend.fetch_thread_messages(CHANNEL_ID, THREAD_TS)]

        assert texts == ["page one", "page two"]
        assert backend.slack_client.conversations_replies.call_args.kwargs["cursor"] == "c1"

    def test_stops_paging_at_the_page_cap(self, backend: ModuleType) -> None:
        backend.slack_client.conversations_replies.return_value = {
            "messages": [human("more")],
            "has_more": True,
            "response_metadata": {"next_cursor": "c"},
        }

        backend.fetch_thread_messages(CHANNEL_ID, THREAD_TS)

        assert backend.slack_client.conversations_replies.call_count == backend.MAX_THREAD_PAGES

    def test_keeps_only_the_newest_messages(self, backend: ModuleType) -> None:
        set_thread(backend, *[human(f"m{index}", ts=f"1.{index:04d}") for index in range(500)])

        assert len(backend.fetch_thread_messages(CHANNEL_ID, THREAD_TS)) == backend.MAX_THREAD_MESSAGES


class TestBuildConversation:
    def test_maps_people_to_user_turns_and_the_bot_to_assistant(self, backend: ModuleType) -> None:
        messages = [human("質問", ts="1.0"), bot("回答", ts="2.0"), human("追加質問", ts="3.0")]

        assert backend.build_conversation(messages) == [
            {"role": "user", "content": "質問"},
            {"role": "assistant", "content": "回答"},
            {"role": "user", "content": "追加質問"},
        ]

    def test_folds_consecutive_human_messages_into_one_turn(self, backend: ModuleType) -> None:
        # A thread happily runs several human messages in a row, but Bedrock requires
        # user and assistant turns to alternate.
        messages = [
            human("〇〇について語るスレ", ts="1.0"),
            human("ロググループ保護有効化したほうがいいなー", ts="2.0"),
            human("どうおもう？", ts="3.0"),
        ]

        assert backend.build_conversation(messages) == [
            {
                "role": "user",
                "content": "〇〇について語るスレ\nロググループ保護有効化したほうがいいなー\nどうおもう？",
            }
        ]

    def test_folds_consecutive_bot_messages_too(self, backend: ModuleType) -> None:
        messages = [human("q", ts="1.0"), bot("part one", ts="2.0"), bot("part two", ts="3.0")]

        assert backend.build_conversation(messages)[1] == {"role": "assistant", "content": "part one\npart two"}

    def test_strips_mentions_so_the_model_cannot_echo_a_ping(self, backend: ModuleType) -> None:
        assert backend.build_conversation([human("<@U0BOT> どうおもう？")])[0]["content"] == "どうおもう？"

    def test_drops_the_bots_own_canned_replies(self, backend: ModuleType) -> None:
        # Feeding old failures back as context teaches the model to produce more.
        messages = [human("q", ts="1.0"), bot(backend.EMPTY_INPUT_MESSAGE, ts="2.0"), human("このスレみて", ts="3.0")]

        assert backend.build_conversation(messages) == [{"role": "user", "content": "q\nこのスレみて"}]

    def test_drops_messages_with_no_text(self, backend: ModuleType) -> None:
        messages = [human("q", ts="1.0"), {"type": "message", "user": "U1", "ts": "2.0"}]

        assert backend.build_conversation(messages) == [{"role": "user", "content": "q"}]

    def test_drops_channel_join_noise(self, backend: ModuleType) -> None:
        messages = [{"type": "message", "subtype": "channel_join", "text": "has joined", "user": "U1", "ts": "1.0"}]

        assert backend.build_conversation(messages) == []

    def test_returns_nothing_for_an_empty_thread(self, backend: ModuleType) -> None:
        assert backend.build_conversation([]) == []


class TestEventClaims:
    def test_claims_an_event_nobody_holds(self, backend: ModuleType) -> None:
        assert backend.claim_event(EVENT_ID) is True

    def test_keys_the_claim_by_event_id(self, backend: ModuleType) -> None:
        backend.claim_event(EVENT_ID)

        assert backend.idempotency_table.put_item.call_args.kwargs["Item"]["event_id"] == EVENT_ID

    def test_refuses_an_event_another_invocation_already_claimed(self, backend: ModuleType) -> None:
        backend.idempotency_table.put_item.side_effect = conditional_check_failed("PutItem")

        assert backend.claim_event(EVENT_ID) is False

    def test_lets_a_claim_be_retaken_once_it_has_expired(self, backend: ModuleType) -> None:
        # The condition only rejects a claim that is still live, so an invocation
        # killed mid-flight cannot swallow the question forever.
        backend.claim_event(EVENT_ID)

        kwargs = backend.idempotency_table.put_item.call_args.kwargs
        assert "claim_expires_at < :now" in kwargs["ConditionExpression"]
        assert kwargs["ExpressionAttributeValues"][":now"] <= int(time.time())

    def test_claim_expiry_matches_the_queue_visibility_timeout(self, backend: ModuleType) -> None:
        assert backend.CLAIM_TTL_SECONDS == 180

        before = int(time.time())
        backend.claim_event(EVENT_ID)

        expires = backend.idempotency_table.put_item.call_args.kwargs["Item"]["claim_expires_at"]
        assert expires >= before + backend.CLAIM_TTL_SECONDS

    def test_propagates_errors_that_are_not_a_failed_condition(self, backend: ModuleType) -> None:
        backend.idempotency_table.put_item.side_effect = RuntimeError("boom")

        with pytest.raises(RuntimeError):
            backend.claim_event(EVENT_ID)

    def test_releasing_a_claim_deletes_it(self, backend: ModuleType) -> None:
        backend.release_event_claim(EVENT_ID)

        backend.idempotency_table.delete_item.assert_called_once_with(Key={"event_id": EVENT_ID})


class TestTrimHistory:
    def test_returns_a_short_history_untouched(self, backend: ModuleType) -> None:
        messages = conversation(4)

        assert backend.trim_history(messages) == messages

    def test_keeps_the_newest_messages(self, backend: ModuleType) -> None:
        messages = conversation(30)

        trimmed = backend.trim_history(messages)

        assert trimmed[-1] == messages[-1]
        assert trimmed == messages[-len(trimmed) :]

    def test_never_exceeds_the_message_limit(self, backend: ModuleType) -> None:
        assert len(backend.trim_history(conversation(100))) <= backend.MAX_HISTORY_MESSAGES

    def test_drops_an_assistant_reply_left_at_the_head_of_the_window(self, backend: ModuleType) -> None:
        messages = conversation(backend.MAX_HISTORY_MESSAGES + 1)
        assert messages[1]["role"] == "assistant"

        trimmed = backend.trim_history(messages)

        assert trimmed[0] == messages[2]
        assert len(trimmed) == backend.MAX_HISTORY_MESSAGES - 1

    def test_handles_an_empty_history(self, backend: ModuleType) -> None:
        assert backend.trim_history([]) == []

    def test_drops_old_turns_once_the_size_budget_is_exceeded(self, backend: ModuleType) -> None:
        big = "x" * (backend.MAX_HISTORY_CHARACTERS // 4)
        messages = [{"role": "user" if index % 2 == 0 else "assistant", "content": big} for index in range(8)]

        trimmed = backend.trim_history(messages)

        assert len(trimmed) < len(messages)
        assert sum(len(message["content"]) for message in trimmed) <= backend.MAX_HISTORY_CHARACTERS

    def test_keeps_the_newest_message_even_when_it_alone_blows_the_budget(self, backend: ModuleType) -> None:
        messages = [{"role": "user", "content": "x" * (backend.MAX_HISTORY_CHARACTERS * 2)}]

        assert backend.trim_history(messages) == messages


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


class TestLambdaHandlerAnswersFromTheThread:
    @pytest.fixture(autouse=True)
    def _bedrock_answers(self, backend: ModuleType) -> None:
        backend.bedrock_runtime.converse.return_value = bedrock_response("それは有効化しておくべきですね")

    def test_answers_a_bare_mention_using_what_the_thread_already_says(self, backend: ModuleType) -> None:
        # The whole point: "どうおもう？" on its own is unanswerable, but the thread
        # around it is not.
        set_thread(
            backend,
            human("〇〇について語るスレ", ts="1700000000.000100"),
            human("ロググループ保護有効化したほうがいいなー", ts="1700000000.000200"),
            human("<@U0BOT> どうおもう？", ts=MESSAGE_TS),
        )

        backend.lambda_handler(mention("どうおもう？"), None)

        sent = sent_conversation(backend)
        assert len(sent) == 1
        assert "ロググループ保護有効化したほうがいいなー" in sent[0]["content"]
        assert "〇〇について語るスレ" in sent[0]["content"]

    def test_includes_its_own_earlier_replies_as_assistant_turns(self, backend: ModuleType) -> None:
        set_thread(
            backend,
            human("内包表記とは？", ts="1.0"),
            bot("リスト内包表記は…", ts="2.0"),
            human("<@U0BOT> じゃあ辞書版は？", ts=MESSAGE_TS),
        )

        backend.lambda_handler(mention("じゃあ辞書版は？"), None)

        assert sent_conversation(backend) == [
            {"role": "user", "content": "内包表記とは？"},
            {"role": "assistant", "content": "リスト内包表記は…"},
            {"role": "user", "content": "じゃあ辞書版は？"},
        ]

    def test_replies_in_the_thread(self, backend: ModuleType) -> None:
        set_thread(backend, human("<@U0BOT> どうおもう？", ts=MESSAGE_TS))

        backend.lambda_handler(mention(), None)

        backend.slack_client.chat_postMessage.assert_called_once_with(
            channel=CHANNEL_ID,
            thread_ts=THREAD_TS,
            text="それは有効化しておくべきですね",
        )

    def test_returns_200(self, backend: ModuleType) -> None:
        set_thread(backend, human("<@U0BOT> どうおもう？", ts=MESSAGE_TS))

        assert backend.lambda_handler(mention(), None)["statusCode"] == 200

    def test_adds_the_mention_when_slack_has_not_caught_up_yet(self, backend: ModuleType) -> None:
        # Slack reads are eventually consistent, so the triggering message may be
        # missing from the thread. Without this the bot answers the wrong message.
        set_thread(backend, human("earlier context", ts="1.0"))

        backend.lambda_handler(mention("どうおもう？"), None)

        assert sent_conversation(backend) == [{"role": "user", "content": "earlier context\nどうおもう？"}]

    def test_does_not_duplicate_the_mention_when_the_thread_has_it(self, backend: ModuleType) -> None:
        set_thread(backend, human("どうおもう？", ts=MESSAGE_TS))

        backend.lambda_handler(mention("どうおもう？"), None)

        assert sent_conversation(backend) == [{"role": "user", "content": "どうおもう？"}]

    def test_answers_from_the_mention_alone_when_the_thread_cannot_be_read(self, backend: ModuleType) -> None:
        # A missing history scope should degrade the answer, not break the bot.
        backend.slack_client.conversations_replies.side_effect = RuntimeError("missing_scope")

        result = backend.lambda_handler(mention("これどう？"), None)

        assert result["statusCode"] == 200
        assert sent_conversation(backend) == [{"role": "user", "content": "これどう？"}]

    def test_no_longer_writes_conversation_state(self, backend: ModuleType) -> None:
        # Slack is the only store of the conversation; DynamoDB holds claims alone.
        set_thread(backend, human("<@U0BOT> どうおもう？", ts=MESSAGE_TS))

        backend.lambda_handler(mention(), None)

        written = [call.kwargs["Item"] for call in backend.idempotency_table.put_item.call_args_list]
        assert all("messages" not in item for item in written)
        assert [item["event_id"] for item in written] == [EVENT_ID]


class TestLambdaHandlerWithNothingToAnswer:
    def test_reports_an_empty_question_when_the_thread_is_empty_too(self, backend: ModuleType) -> None:
        set_thread(backend)

        result = backend.lambda_handler(mention(""), None)

        assert result["statusCode"] == 400
        backend.slack_client.chat_postMessage.assert_called_once_with(
            channel=CHANNEL_ID,
            thread_ts=THREAD_TS,
            text=backend.EMPTY_INPUT_MESSAGE,
        )
        backend.bedrock_runtime.converse.assert_not_called()

    def test_answers_a_bare_mention_when_the_thread_has_context(self, backend: ModuleType) -> None:
        # An empty mention used to be rejected outright; now the thread can carry it.
        set_thread(backend, human("語るスレ", ts="1.0"))
        backend.bedrock_runtime.converse.return_value = bedrock_response("answer")

        assert backend.lambda_handler(mention(""), None)["statusCode"] == 200


class TestLambdaHandlerDeduplicates:
    @pytest.fixture(autouse=True)
    def _bedrock_answers(self, backend: ModuleType) -> None:
        set_thread(backend, human("<@U0BOT> どうおもう？", ts=MESSAGE_TS))
        backend.bedrock_runtime.converse.return_value = bedrock_response("answer")

    def test_claims_the_event_before_doing_any_work(self, backend: ModuleType) -> None:
        backend.lambda_handler(mention(), None)

        assert backend.idempotency_table.put_item.call_args_list[0].kwargs["Item"]["event_id"] == EVENT_ID

    def test_skips_an_event_another_invocation_already_claimed(self, backend: ModuleType) -> None:
        backend.idempotency_table.put_item.side_effect = conditional_check_failed("PutItem")

        result = backend.lambda_handler(mention(), None)

        assert result["statusCode"] == 200
        backend.bedrock_runtime.converse.assert_not_called()
        backend.slack_client.chat_postMessage.assert_not_called()

    def test_a_redelivered_message_is_answered_only_once(self, backend: ModuleType) -> None:
        backend.idempotency_table = FakeIdempotencyTable()

        backend.lambda_handler(mention(), None)
        backend.lambda_handler(mention(), None)

        assert backend.slack_client.chat_postMessage.call_count == 1

    def test_a_different_question_in_the_same_thread_is_still_answered(self, backend: ModuleType) -> None:
        backend.idempotency_table = FakeIdempotencyTable()

        backend.lambda_handler(mention("one", event_id="Ev1"), None)
        backend.lambda_handler(mention("two", event_id="Ev2"), None)

        assert backend.slack_client.chat_postMessage.call_count == 2

    def test_releases_the_claim_when_bedrock_fails_so_sqs_can_retry(self, backend: ModuleType) -> None:
        table = FakeIdempotencyTable()
        backend.idempotency_table = table
        backend.bedrock_runtime.converse.side_effect = RuntimeError("ThrottlingException")

        with pytest.raises(RuntimeError):
            backend.lambda_handler(mention(), None)

        assert EVENT_ID not in table.items

    def test_the_retry_after_a_failure_goes_through(self, backend: ModuleType) -> None:
        backend.idempotency_table = FakeIdempotencyTable()
        backend.bedrock_runtime.converse.side_effect = [RuntimeError("throttled"), bedrock_response("answer")]

        with pytest.raises(RuntimeError):
            backend.lambda_handler(mention(), None)
        result = backend.lambda_handler(mention(), None)

        assert result["statusCode"] == 200
        assert backend.slack_client.chat_postMessage.call_count == 1

    def test_releases_the_claim_when_the_slack_post_fails(self, backend: ModuleType) -> None:
        table = FakeIdempotencyTable()
        backend.idempotency_table = table
        backend.slack_client.chat_postMessage.side_effect = RuntimeError("slack down")

        with pytest.raises(RuntimeError):
            backend.lambda_handler(mention(), None)

        assert EVENT_ID not in table.items

    def test_still_works_for_a_message_without_an_event_id(self, backend: ModuleType) -> None:
        event = sqs_event({"channel_id": CHANNEL_ID, "thread_ts": THREAD_TS, "input_text": "hi"})

        assert backend.lambda_handler(event, None)["statusCode"] == 200
        backend.slack_client.chat_postMessage.assert_called_once()


class TestLambdaHandlerEnforcesTheAnswerDeadline:
    @pytest.fixture(autouse=True)
    def _bedrock_answers(self, backend: ModuleType) -> None:
        set_thread(backend, human("<@U0BOT> どうおもう？", ts=MESSAGE_TS))
        backend.bedrock_runtime.converse.return_value = bedrock_response("answer")

    def test_answers_a_question_that_is_still_fresh(self, backend: ModuleType) -> None:
        backend.lambda_handler(mention(age_seconds=backend.ANSWER_DEADLINE_SECONDS - 5), None)

        backend.bedrock_runtime.converse.assert_called_once()

    def test_does_not_answer_a_question_that_aged_out(self, backend: ModuleType) -> None:
        backend.lambda_handler(mention(age_seconds=backend.ANSWER_DEADLINE_SECONDS + 5), None)

        backend.bedrock_runtime.converse.assert_not_called()

    def test_tells_the_thread_it_gave_up_instead_of_going_quiet(self, backend: ModuleType) -> None:
        backend.lambda_handler(mention(age_seconds=backend.ANSWER_DEADLINE_SECONDS + 5), None)

        backend.slack_client.chat_postMessage.assert_called_once_with(
            channel=CHANNEL_ID,
            thread_ts=THREAD_TS,
            text=backend.DEADLINE_MESSAGE,
        )

    def test_does_not_even_read_the_thread_for_an_expired_question(self, backend: ModuleType) -> None:
        backend.lambda_handler(mention(age_seconds=backend.ANSWER_DEADLINE_SECONDS + 5), None)

        backend.slack_client.conversations_replies.assert_not_called()

    def test_deadline_is_shorter_than_the_queue_visibility_timeout(self, backend: ModuleType) -> None:
        assert backend.ANSWER_DEADLINE_SECONDS < backend.CLAIM_TTL_SECONDS

    def test_answers_a_message_that_predates_the_timestamp(self, backend: ModuleType) -> None:
        event = sqs_event({"channel_id": CHANNEL_ID, "thread_ts": THREAD_TS, "input_text": "hi"})

        backend.lambda_handler(event, None)

        backend.bedrock_runtime.converse.assert_called_once()


class TestLambdaHandlerTrimsLongThreads:
    @pytest.fixture(autouse=True)
    def _bedrock_answers(self, backend: ModuleType) -> None:
        backend.bedrock_runtime.converse.return_value = bedrock_response("answer")

    def _alternating_thread(self, turns: int) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        for index in range(turns):
            builder = human if index % 2 == 0 else bot
            messages.append(builder(f"message {index}", ts=f"1.{index:04d}"))
        return messages

    def test_caps_a_long_thread_at_the_history_limit(self, backend: ModuleType) -> None:
        set_thread(backend, *self._alternating_thread(40))

        backend.lambda_handler(mention("next"), None)

        assert len(sent_conversation(backend)) <= backend.MAX_HISTORY_MESSAGES

    def test_drops_the_oldest_turns_and_keeps_the_newest(self, backend: ModuleType) -> None:
        set_thread(backend, *self._alternating_thread(40))

        backend.lambda_handler(mention("next"), None)

        sent = sent_conversation(backend)
        assert "message 0" not in sent[0]["content"]
        assert "message 39" in sent[-1]["content"] or "message 39" in sent[-2]["content"]

    @pytest.mark.parametrize("turns", range(1, 42))
    def test_always_sends_a_conversation_that_starts_with_the_user(self, backend: ModuleType, turns: int) -> None:
        # Bedrock rejects a conversation whose first message is from the assistant.
        set_thread(backend, *self._alternating_thread(turns))

        backend.lambda_handler(mention("next"), None)

        assert sent_conversation(backend)[0]["role"] == "user"

    @pytest.mark.parametrize("turns", range(1, 42))
    def test_always_sends_strictly_alternating_roles(self, backend: ModuleType, turns: int) -> None:
        set_thread(backend, *self._alternating_thread(turns))

        backend.lambda_handler(mention("next"), None)

        roles = [message["role"] for message in sent_conversation(backend)]
        assert all(before != after for before, after in zip(roles, roles[1:], strict=False))

    def test_a_messy_multi_person_thread_still_produces_a_valid_conversation(self, backend: ModuleType) -> None:
        # Runs of human messages, bot replies, joins and blank messages all mixed.
        messages: list[dict[str, Any]] = []
        for index in range(60):
            if index % 7 == 0:
                messages.append(bot(f"bot {index}", ts=f"1.{index:04d}"))
            elif index % 11 == 0:
                messages.append({"type": "message", "subtype": "channel_join", "text": "j", "ts": f"1.{index:04d}"})
            else:
                messages.append(human(f"human {index}", ts=f"1.{index:04d}"))
        set_thread(backend, *messages)

        backend.lambda_handler(mention("まとめて"), None)

        sent = sent_conversation(backend)
        roles = [message["role"] for message in sent]
        assert roles[0] == "user"
        assert all(before != after for before, after in zip(roles, roles[1:], strict=False))
        assert len(sent) <= backend.MAX_HISTORY_MESSAGES


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
