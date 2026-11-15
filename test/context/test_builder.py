"""ContextBuilder 上下文组装契约。"""
import pytest

from agents.context.builder import ContextBuilder
from agents.context.history import HistoryManager
from agents.context.pruner import ContextPruner
from agents.context.summary import ContextSummarizer
from agents.core.config import Config
from agents.core.message import Message


def _builder() -> ContextBuilder:
    return ContextBuilder(HistoryManager(), Config())


def _contents(context):
    return [message["content"] for message in context]


def test_build_includes_system_and_history_in_order():
    builder = _builder()
    builder.history.append(Message("u", "user"))
    builder.history.append(Message("a", "assistant"))

    assert builder.build("SYS") == [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "u"},
        {"role": "assistant", "content": "a"},
    ]


def test_build_without_system_prompt_omits_system_message():
    builder = _builder()
    builder.history.append(Message("u", "user"))

    assert builder.build() == [{"role": "user", "content": "u"}]
    assert builder.build("") == [{"role": "user", "content": "u"}]


def test_build_preserves_tool_calls_and_tool_call_id():
    builder = _builder()
    tool_calls = [{"id": "c1", "type": "function", "function": {"name": "Bash", "arguments": "{}"}}]
    builder.history.append(Message("a", "assistant", tool_calls=tool_calls))
    builder.history.append(Message("ok", "tool", tool_call_id="c1"))

    context = builder.build()

    assert context[0]["tool_calls"] == tool_calls
    assert context[1] == {"role": "tool", "content": "ok", "tool_call_id": "c1"}


def test_all_switches_off_drops_summary_messages():
    builder = _builder()
    builder.history.append(Message("m0", "user"))
    builder.history.append(Message("S", "summary", replace=[0, 0]))

    assert _contents(builder.build("SYS")) == ["SYS", "m0"]


def test_all_switches_off_does_not_warn(capsys):
    builder = _builder()
    builder.history.append(Message("u", "user"))

    builder.build("SYS")

    assert capsys.readouterr().out == ""


def test_summary_switch_on_keeps_summary_messages(capsys):
    builder = ContextBuilder(HistoryManager(), Config(context_summary_enabled=True))
    builder.history.append(Message("m0", "user"))
    builder.history.append(Message("S", "summary", replace=[0, 0]))

    context = builder.build("SYS")

    # 本轮 summary 为空实现，不做 replace 遮蔽，摘要与原文都保留。
    assert _contents(context) == ["SYS", "m0", "S"]
    assert context[2]["role"] == "user"
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize(
    "switch",
    ["context_truncation_enabled", "context_recall_enabled"],
)
def test_non_summary_switch_still_drops_summary(switch, capsys):
    builder = ContextBuilder(HistoryManager(), Config(**{switch: True}))
    builder.history.append(Message("m0", "user"))
    builder.history.append(Message("S", "summary", replace=[0, 0]))

    assert _contents(builder.build("SYS")) == ["SYS", "m0"]
    assert capsys.readouterr().out == ""


def test_pruner_stub_returns_input_unchanged():
    messages = [Message("u", "user")]

    assert ContextPruner(Config()).prune(messages) == messages


def test_summarizer_stub_returns_none():
    assert ContextSummarizer(Config()).maybe_summarize([Message("u", "user")]) is None


def test_build_appends_extra_messages_without_recording_them():
    builder = _builder()
    builder.history.append(Message("u", "user"))
    extra = [{"role": "user", "content": "CONCLUDE"}]

    context = builder.build("SYS", extra_messages=extra)

    assert context[-1] is extra[0]
    assert all(message.content != "CONCLUDE" for message in builder.history.get_history())


def test_build_returns_a_new_list_each_call():
    builder = _builder()
    builder.history.append(Message("u", "user"))

    first = builder.build("SYS")
    first.append({"role": "user", "content": "mutated"})

    assert builder.build("SYS") == [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "u"},
    ]


def test_estimate_message_tokens_uses_rendered_characters_over_four():
    builder = _builder()
    message = Message("x" * 400, "user")

    # "[user] " (7 字符) + 400 字符 = 407；407 // 4 = 101。
    assert builder.estimate_message_tokens(message) == 101


def test_should_compress_uses_measured_tokens_strictly():
    builder = ContextBuilder(
        HistoryManager(),
        Config(context_window=100, compression_threshold=0.5),
    )
    builder.history.append(Message("u", "user"))

    builder.record_prompt_tokens(50)
    assert builder.should_compress() is False

    builder.record_prompt_tokens(51)
    assert builder.should_compress() is True


def test_should_compress_counts_unmeasured_tail():
    builder = ContextBuilder(
        HistoryManager(),
        Config(context_window=100, compression_threshold=0.5),
    )
    builder.history.append(Message("u", "user"))
    builder.record_prompt_tokens(10)
    builder.history.append(Message("x" * 400, "user"))

    assert builder.should_compress() is True


def test_clear_resets_measured_tokens():
    builder = ContextBuilder(
        HistoryManager(),
        Config(context_window=100, compression_threshold=0.5),
    )
    builder.record_prompt_tokens(60)
    assert builder.should_compress() is True

    builder.history.clear()

    assert builder.should_compress() is False


def test_load_resets_measured_tokens():
    builder = ContextBuilder(
        HistoryManager(),
        Config(context_window=100, compression_threshold=0.5),
    )
    builder.record_prompt_tokens(60)
    assert builder.should_compress() is True

    builder.history.load_from_dict({"history": [], "next_id": 0})

    assert builder.should_compress() is False
