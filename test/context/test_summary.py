"""ContextSummarizer 摘要遮蔽算法契约。"""
from agents.context.summary import ContextSummarizer
from agents.core.config import Config
from agents.core.message import Message


def _summarizer() -> ContextSummarizer:
    return ContextSummarizer(Config())


def test_no_summary_keeps_order_and_returns_new_list():
    messages = [Message("u", "user", id=0), Message("a", "assistant", id=1)]

    masked = _summarizer().apply_summary_masking(messages)

    assert [message.content for message in masked] == ["u", "a"]
    assert masked is not messages
    assert masked[0] is messages[0]
    assert masked[1] is messages[1]


def test_summary_removes_replaced_messages_and_front_inserts():
    messages = [
        Message("m0", "user", id=0),
        Message("m1", "assistant", id=1),
        Message("S", "summary", id=2, replace=[0]),
    ]

    masked = _summarizer().apply_summary_masking(messages)

    assert [message.content for message in masked] == ["S", "m1"]
    assert masked[0] is messages[2]


def test_summary_removes_non_contiguous_ids():
    messages = [
        Message("m0", "user", id=0),
        Message("m1", "assistant", id=1),
        Message("m2", "tool", id=2),
        Message("m3", "assistant", id=3),
        Message("S", "summary", id=4, replace=[0, 2]),
    ]

    masked = _summarizer().apply_summary_masking(messages)

    assert [message.content for message in masked] == ["S", "m1", "m3"]


def test_summary_with_empty_replace_keeps_all_messages():
    messages = [
        Message("m0", "user", id=0),
        Message("S", "summary", id=1, replace=[]),
    ]

    masked = _summarizer().apply_summary_masking(messages)

    assert [message.content for message in masked] == ["S", "m0"]


def test_summary_in_the_middle_removes_earlier_messages_and_moves_to_front():
    messages = [
        Message("m0", "user", id=0),
        Message("S", "summary", id=1, replace=[0]),
        Message("m1", "assistant", id=2),
    ]

    masked = _summarizer().apply_summary_masking(messages)

    assert [message.content for message in masked] == ["S", "m1"]


def test_missing_replace_ids_are_noop():
    messages = [
        Message("m0", "user", id=0),
        Message("S", "summary", id=1, replace=[99]),
    ]

    masked = _summarizer().apply_summary_masking(messages)

    assert [message.content for message in masked] == ["S", "m0"]


def test_duplicate_replace_ids_remove_message_once():
    messages = [
        Message("m0", "user", id=0),
        Message("S", "summary", id=1, replace=[0, 0]),
    ]

    masked = _summarizer().apply_summary_masking(messages)

    assert [message.content for message in masked] == ["S"]


def test_input_list_and_messages_are_not_mutated():
    messages = [
        Message("m0", "user", id=0),
        Message("S", "summary", id=1, replace=[0]),
    ]

    _summarizer().apply_summary_masking(messages)

    assert [message.content for message in messages] == ["m0", "S"]
    assert messages[1].replace == [0]


def test_two_summary_chain_keeps_hidden_messages_hidden():
    messages = [
        Message("m0", "user", id=0),
        Message("S1", "summary", id=1, replace=[0]),
        Message("m1", "assistant", id=2),
        Message("m2", "user", id=3),
        Message("S2", "summary", id=4, replace=[1, 2]),
    ]

    masked = _summarizer().apply_summary_masking(messages)

    assert [message.content for message in masked] == ["S2", "m2"]


def test_select_region_without_summary_retreats_to_user():
    messages = [
        Message("u0", "user", id=0),
        Message("a1", "assistant", id=1),
        Message("u2", "user", id=2),
        Message("a3", "assistant", id=3),
        Message("u4", "user", id=4),
        Message("a5", "assistant", id=5),
    ]
    summarizer = ContextSummarizer(Config(context_summary_keep_rounds=2))

    selected, keep = summarizer.select_summary_region(messages)

    assert [message.content for message in selected] == ["u0", "a1"]
    assert [message.content for message in keep] == ["u2", "a3", "u4", "a5"]


def test_select_region_with_summary_keeps_summary_in_selected():
    messages = [
        Message("S", "summary", id=0, replace=[9]),
        Message("u1", "user", id=1),
        Message("a2", "assistant", id=2),
        Message("u3", "user", id=3),
        Message("a4", "assistant", id=4),
        Message("u5", "user", id=5),
        Message("a6", "assistant", id=6),
    ]
    summarizer = ContextSummarizer(Config(context_summary_keep_rounds=2))

    selected, keep = summarizer.select_summary_region(messages)

    assert [message.content for message in selected] == ["S", "u1", "a2"]
    assert [message.content for message in keep] == ["u3", "a4", "u5", "a6"]


def test_select_region_without_user_before_boundary_starts_at_assistant():
    messages = [
        Message("a1", "assistant", id=0),
        Message("a2", "assistant", id=1),
        Message("a3", "assistant", id=2),
    ]
    summarizer = ContextSummarizer(Config(context_summary_keep_rounds=2))

    selected, keep = summarizer.select_summary_region(messages)

    assert [message.content for message in selected] == ["a1"]
    assert [message.content for message in keep] == ["a2", "a3"]


def test_select_region_keep_rounds_is_configurable():
    messages = [
        Message("u0", "user", id=0),
        Message("a1", "assistant", id=1),
        Message("u2", "user", id=2),
        Message("a3", "assistant", id=3),
    ]
    summarizer = ContextSummarizer(Config(context_summary_keep_rounds=1))

    selected, keep = summarizer.select_summary_region(messages)

    assert [message.content for message in selected] == ["u0", "a1"]
    assert [message.content for message in keep] == ["u2", "a3"]


def test_summarize_without_llm_returns_degraded_outcome():
    compression = [Message("m0", "user", id=0), Message("m1", "assistant", id=1)]

    outcome = ContextSummarizer(Config()).summarize(compression)
    summary = outcome.message

    assert summary.role == "summary"
    assert summary.content.startswith("【历史对话摘要（降级）】")
    assert "- [user] id=0" in summary.content
    assert "- [assistant] id=1" in summary.content
    assert summary.replace == [0, 1]
    assert summary.id is None
    assert outcome.mode == "normal"
    assert outcome.usage is None
    assert outcome.degraded is True
    assert outcome.error


def test_summarize_incremental_without_llm_returns_degraded_outcome():
    previous = Message("S1", "summary", id=5, replace=[0, 1])
    compression = [Message("m6", "user", id=6), Message("m7", "assistant", id=7)]

    outcome = ContextSummarizer(Config()).summarize_incremental(
        previous, compression
    )
    summary = outcome.message

    assert summary.role == "summary"
    assert summary.content.startswith("S1\n\n【新增已压缩消息（降级）】")
    assert "- [user] id=6" in summary.content
    assert "- [assistant] id=7" in summary.content
    assert summary.replace == [5, 6, 7]
    assert summary.id is None
    assert outcome.mode == "incremental"
    assert outcome.usage is None
    assert outcome.degraded is True
    assert outcome.error
