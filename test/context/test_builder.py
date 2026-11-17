"""ContextBuilder 上下文组装契约。"""
import pytest

from agents.context.builder import ContextBuilder
from agents.context.history import HistoryManager
from agents.core.config import Config
from agents.core.message import Message


def _builder() -> ContextBuilder:
    return ContextBuilder(HistoryManager(), Config())


def _contents(context):
    return [message["content"] for message in context]


def _pruning_builder(**overrides) -> ContextBuilder:
    config = {
        "context_truncation_rounds": 2,
        "context_truncation_threshold": 20,
        "context_truncation_head_chars": 10,
        "context_truncation_tail_chars": 5,
    }
    config.update(overrides)
    return ContextBuilder(
        HistoryManager(),
        Config(context_truncation_enabled=True, **config),
    )


def _summary_builder(**overrides) -> ContextBuilder:
    config = {
        "context_window": 100,
        "compression_threshold": 0.5,
        "context_summary_enabled": True,
        "context_summary_keep_rounds": 2,
    }
    config.update(overrides)
    return ContextBuilder(HistoryManager(), Config(**config))


def _seed_three_rounds(builder: ContextBuilder) -> None:
    for content, role in (
        ("u0", "user"),
        ("a1", "assistant"),
        ("u2", "user"),
        ("a3", "assistant"),
        ("u4", "user"),
        ("a5", "assistant"),
    ):
        builder.history.append(Message(content, role))


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
    builder.history.append(Message("S", "summary", replace=[0]))

    assert _contents(builder.build("SYS")) == ["SYS", "m0"]


def test_all_switches_off_does_not_warn(capsys):
    builder = _builder()
    builder.history.append(Message("u", "user"))

    builder.build("SYS")

    assert capsys.readouterr().out == ""


def test_summary_switch_on_masks_replaced_messages(capsys):
    builder = ContextBuilder(HistoryManager(), Config(context_summary_enabled=True))
    builder.history.append(Message("m0", "user"))
    builder.history.append(Message("S", "summary", replace=[0]))

    context = builder.build("SYS")

    # replace 按完整 id 列表消费：m0 被遮蔽，摘要前插并映射为 user。
    assert _contents(context) == ["SYS", "S"]
    assert context[1]["role"] == "user"
    assert capsys.readouterr().out == ""


def test_summary_switch_on_fronts_summary_before_unmasked_messages():
    builder = ContextBuilder(HistoryManager(), Config(context_summary_enabled=True))
    builder.history.append(Message("m0", "user"))
    builder.history.append(Message("m1", "assistant"))
    builder.history.append(Message("S", "summary", replace=[0]))

    context = builder.build("SYS")

    assert _contents(context) == ["SYS", "S", "m1"]
    assert [message["role"] for message in context] == [
        "system",
        "user",
        "assistant",
    ]


def test_summary_masking_does_not_alter_history():
    builder = ContextBuilder(HistoryManager(), Config(context_summary_enabled=True))
    builder.history.append(Message("m0", "user"))
    builder.history.append(Message("S", "summary", replace=[0]))

    builder.build("SYS")

    history = builder.history.get_history()
    assert [message.content for message in history] == ["m0", "S"]
    assert history[1].replace == [0]


def test_summary_masking_does_not_touch_extra_messages():
    builder = ContextBuilder(HistoryManager(), Config(context_summary_enabled=True))
    builder.history.append(Message("m0", "user"))
    builder.history.append(Message("S", "summary", replace=[0]))
    extra = [{"role": "user", "content": "E"}]

    context = builder.build("SYS", extra_messages=extra)

    assert context[-1] is extra[0]
    assert context[-1]["content"] == "E"


@pytest.mark.parametrize(
    "switch",
    ["context_truncation_enabled", "context_recall_enabled"],
)
def test_non_summary_switch_still_drops_summary(switch, capsys):
    builder = ContextBuilder(HistoryManager(), Config(**{switch: True}))
    builder.history.append(Message("m0", "user"))
    builder.history.append(Message("S", "summary", replace=[0]))

    assert _contents(builder.build("SYS")) == ["SYS", "m0"]
    assert capsys.readouterr().out == ""


def test_config_pruning_defaults():
    config = Config()

    assert config.context_truncation_rounds == 10
    assert config.context_truncation_threshold == 2000
    assert config.context_truncation_head_chars == 1500
    assert config.context_truncation_tail_chars == 500
    assert config.context_truncation_enabled is False


def test_config_keeps_valid_pruning_values():
    config = Config(
        context_truncation_rounds=3,
        context_truncation_threshold=100,
        context_truncation_head_chars=60,
        context_truncation_tail_chars=40,
        context_truncation_enabled=True,
    )

    assert config.context_truncation_rounds == 3
    assert config.context_truncation_threshold == 100
    assert config.context_truncation_head_chars == 60
    assert config.context_truncation_tail_chars == 40
    assert config.context_truncation_enabled is True


@pytest.mark.parametrize(
    "overrides",
    [
        {"context_truncation_rounds": -1},
        {"context_truncation_threshold": 0},
        {"context_truncation_head_chars": -1},
        {"context_truncation_tail_chars": -1},
        {"context_truncation_head_chars": 2000, "context_truncation_tail_chars": 500},
    ],
)
def test_invalid_pruning_config_falls_back_to_defaults(overrides):
    config = Config(context_truncation_enabled=True, **overrides)

    assert config.context_truncation_enabled is True
    assert config.context_truncation_rounds == 10
    assert config.context_truncation_threshold == 2000
    assert config.context_truncation_head_chars == 1500
    assert config.context_truncation_tail_chars == 500


def test_invalid_pruning_config_resets_all_four_values():
    config = Config(
        context_truncation_rounds=3,
        context_truncation_threshold=100,
        context_truncation_head_chars=-1,
        context_truncation_tail_chars=40,
    )

    assert config.context_truncation_rounds == 10
    assert config.context_truncation_threshold == 2000
    assert config.context_truncation_head_chars == 1500
    assert config.context_truncation_tail_chars == 500


def test_config_summary_keep_rounds_defaults_to_ten():
    assert Config().context_summary_keep_rounds == 10


def test_config_keeps_valid_summary_keep_rounds():
    assert Config(context_summary_keep_rounds=3).context_summary_keep_rounds == 3


@pytest.mark.parametrize("keep_rounds", [0, -1])
def test_invalid_summary_keep_rounds_falls_back_to_ten(keep_rounds):
    config = Config(
        context_summary_enabled=True,
        context_summary_keep_rounds=keep_rounds,
    )

    assert config.context_summary_keep_rounds == 10
    assert config.context_summary_enabled is True


def test_pruner_keeps_short_recent_tool_content():
    builder = _pruning_builder()
    builder.history.append(Message("u", "user"))
    builder.history.append(Message("a", "assistant"))
    builder.history.append(Message("short", "tool", tool_call_id="c1"))

    context = builder.build()

    assert context[-1]["content"] == "short"
    assert context[-1]["tool_call_id"] == "c1"


def test_pruner_wraps_recent_long_tool_content_with_newlines():
    builder = _pruning_builder()
    builder.history.append(Message("u", "user"))
    builder.history.append(Message("a", "assistant"))
    original = "H" * 10 + "M" * 35 + "T" * 5
    builder.history.append(Message(original, "tool", tool_call_id="c1"))

    context = builder.build("SYS")

    expected = (
        "H" * 10
        + "\n"
        + "已裁剪，详情参见记忆2中偏移10，长度35"
        + "\n"
        + "T" * 5
    )
    assert context[-1]["content"] == expected
    assert context[-1]["tool_call_id"] == "c1"
    assert builder.history.get_history()[-1].content == original


def test_pruner_keeps_tool_after_one_assistant_when_two_rounds_configured():
    builder = _pruning_builder()
    builder.history.append(Message("u", "user"))
    builder.history.append(Message("a1", "assistant"))
    original = "H" * 10 + "M" * 35 + "T" * 5
    builder.history.append(Message(original, "tool", tool_call_id="c1"))
    builder.history.append(Message("a2", "assistant"))

    context = builder.build()

    expected = (
        "H" * 10
        + "\n"
        + "已裁剪，详情参见记忆2中偏移10，长度35"
        + "\n"
        + "T" * 5
    )
    assert context[2]["content"] == expected


def test_pruner_keeps_recent_content_when_candidate_would_grow():
    builder = _pruning_builder()
    builder.history.append(Message("u", "user"))
    builder.history.append(Message("a", "assistant"))
    original = "X" * 20
    builder.history.append(Message(original, "tool", tool_call_id="c1"))

    context = builder.build()

    assert context[-1]["content"] == original


def test_pruner_replaces_old_tool_content_with_marker():
    builder = _pruning_builder()
    builder.history.append(Message("u", "user"))
    builder.history.append(Message("a1", "assistant"))
    original = "H" * 10 + "M" * 35 + "T" * 5
    builder.history.append(Message(original, "tool", tool_call_id="c1"))
    builder.history.append(Message("a2", "assistant"))
    builder.history.append(Message("a3", "assistant"))

    context = builder.build()

    assert context[2]["content"] == "已裁剪，详情参见记忆2中偏移0，长度50"
    assert builder.history.get_history()[2].content == original


def test_pruner_keeps_short_old_tool_content_when_marker_would_grow():
    builder = _pruning_builder()
    builder.history.append(Message("u", "user"))
    builder.history.append(Message("a1", "assistant"))
    builder.history.append(Message("ok", "tool", tool_call_id="c1"))
    builder.history.append(Message("a2", "assistant"))
    builder.history.append(Message("a3", "assistant"))

    context = builder.build()

    assert context[2]["content"] == "ok"


def test_pruner_tail_zero_keeps_trailing_newline():
    builder = _pruning_builder(context_truncation_tail_chars=0)
    builder.history.append(Message("u", "user"))
    builder.history.append(Message("a", "assistant"))
    original = "H" * 10 + "M" * 40
    builder.history.append(Message(original, "tool", tool_call_id="c1"))

    context = builder.build()

    expected = (
        "H" * 10
        + "\n"
        + "已裁剪，详情参见记忆2中偏移10，长度40"
        + "\n"
    )
    assert context[-1]["content"] == expected


def test_pruner_marker_index_includes_summary_before_dropping_it():
    builder = _pruning_builder()
    builder.history.append(Message("u", "user"))
    builder.history.append(Message("S", "summary", replace=[0]))
    builder.history.append(Message("a", "assistant"))
    original = "H" * 10 + "M" * 35 + "T" * 5
    builder.history.append(Message(original, "tool", tool_call_id="c1"))

    context = builder.build()

    expected = (
        "H" * 10
        + "\n"
        + "已裁剪，详情参见记忆3中偏移10，长度35"
        + "\n"
        + "T" * 5
    )
    assert context[-1]["content"] == expected
    assert [message["role"] for message in context] == ["user", "assistant", "tool"]


def test_pruner_does_not_touch_non_tool_messages():
    builder = _pruning_builder(context_summary_enabled=True)
    builder.history.append(Message("U" * 50, "user"))
    builder.history.append(Message("A" * 50, "assistant"))
    original = "H" * 10 + "M" * 35 + "T" * 5
    builder.history.append(Message(original, "tool", tool_call_id="c1"))
    builder.history.append(Message("S" * 50, "summary", replace=[]))

    context = builder.build()

    expected_tool = (
        "H" * 10
        + "\n"
        + "已裁剪，详情参见记忆2中偏移10，长度35"
        + "\n"
        + "T" * 5
    )
    assert context[0]["content"] == "S" * 50
    assert context[1]["content"] == "U" * 50
    assert context[2]["content"] == "A" * 50
    assert context[3]["content"] == expected_tool


def test_pruner_runs_before_masking_and_marker_uses_history_index():
    builder = _pruning_builder(context_summary_enabled=True)
    builder.history.append(Message("u", "user"))
    builder.history.append(Message("a1", "assistant"))
    original = "H" * 10 + "M" * 35 + "T" * 5
    builder.history.append(Message(original, "tool", tool_call_id="c1"))
    builder.history.append(Message("a2", "assistant"))
    builder.history.append(Message("a3", "assistant"))
    builder.history.append(Message("S", "summary", replace=[0, 1]))

    context = builder.build("SYS")

    # 裁剪先于遮蔽：若先遮蔽，pruner 只看到 2 个 assistant，会走最近轮分支。
    assert _contents(context) == [
        "SYS",
        "S",
        "已裁剪，详情参见记忆2中偏移0，长度50",
        "a2",
        "a3",
    ]


def test_pruner_does_not_touch_extra_messages():
    builder = _pruning_builder()
    builder.history.append(Message("u", "user"))
    builder.history.append(Message("a", "assistant"))
    builder.history.append(Message("short", "tool", tool_call_id="c1"))
    extra = [{"role": "tool", "content": "E" * 100, "tool_call_id": "x"}]

    context = builder.build(extra_messages=extra)

    assert context[-1] is extra[0]
    assert context[-1]["content"] == "E" * 100


def test_build_summarizes_compression_region_and_appends_summary():
    builder = _summary_builder()
    _seed_three_rounds(builder)
    builder.record_prompt_tokens(51)

    context = builder.build("SYS")

    assert _contents(context) == ["SYS", "", "u2", "a3", "u4", "a5"]
    assert context[1]["role"] == "user"
    history = builder.history.get_history()
    assert len(history) == 7
    summary = history[-1]
    assert summary.role == "summary"
    assert summary.content == ""
    assert summary.replace == [0, 1]
    assert summary.id == 6


def test_build_without_trigger_does_not_append_summary():
    builder = _summary_builder()
    builder.history.append(Message("m0", "user"))
    builder.history.append(Message("S", "summary", replace=[0]))

    context = builder.build("SYS")

    assert _contents(context) == ["SYS", "S"]
    assert len(builder.history.get_history()) == 2


def test_build_uses_incremental_summary_when_old_summary_exists():
    builder = _summary_builder()
    builder.history.append(Message("m0", "user"))
    builder.history.append(Message("S1", "summary", replace=[0]))
    for content, role in (
        ("u2", "user"),
        ("a3", "assistant"),
        ("u4", "user"),
        ("a5", "assistant"),
        ("u6", "user"),
        ("a7", "assistant"),
    ):
        builder.history.append(Message(content, role))
    builder.record_prompt_tokens(51)

    context = builder.build("SYS")

    assert _contents(context) == ["SYS", "", "u4", "a5", "u6", "a7"]
    history = builder.history.get_history()
    assert history[-1].role == "summary"
    assert history[-1].replace == [1, 2, 3]
    assert history[-1].id == 8

    # 下一次 build 不触发时，遮蔽仍把旧摘要的遮蔽集隐藏起来。
    follow_up = ContextBuilder(builder.history, builder.config)
    assert _contents(follow_up.build("SYS")) == ["SYS", "", "u4", "a5", "u6", "a7"]
    assert len(builder.history.get_history()) == 9


def test_summary_uses_pruned_keep_view():
    builder = _summary_builder(
        context_truncation_enabled=True,
        context_truncation_rounds=2,
        context_truncation_threshold=20,
        context_truncation_head_chars=10,
        context_truncation_tail_chars=5,
        context_summary_keep_rounds=1,
    )
    builder.history.append(Message("u0", "user"))
    builder.history.append(Message("a1", "assistant"))
    builder.history.append(Message("u2", "user"))
    builder.history.append(Message("a3", "assistant"))
    original = "H" * 10 + "M" * 35 + "T" * 5
    builder.history.append(Message(original, "tool", tool_call_id="c1"))
    builder.history.append(Message("a5", "assistant"))
    builder.record_prompt_tokens(51)

    context = builder.build("SYS")

    assert _contents(context) == [
        "SYS",
        "",
        "u2",
        "a3",
        "H" * 10
        + "\n"
        + "已裁剪，详情参见记忆4中偏移10，长度35"
        + "\n"
        + "T" * 5,
        "a5",
    ]
    assert builder.history.get_history()[4].content == original


def test_summary_generation_keeps_extra_messages_temporary():
    builder = _summary_builder()
    _seed_three_rounds(builder)
    builder.record_prompt_tokens(51)
    extra = [{"role": "user", "content": "E"}]

    context = builder.build("SYS", extra_messages=extra)

    assert context[-1] is extra[0]
    assert all(
        message.content != "E" for message in builder.history.get_history()
    )


def test_switch_off_keeps_long_tool_content():
    builder = ContextBuilder(HistoryManager(), Config())
    builder.history.append(Message("u", "user"))
    builder.history.append(Message("a", "assistant"))
    original = "H" * 10 + "M" * 35 + "T" * 5
    builder.history.append(Message(original, "tool", tool_call_id="c1"))

    context = builder.build()

    assert context[-1]["content"] == original


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


def test_should_summarize_uses_measured_tokens_strictly():
    builder = ContextBuilder(
        HistoryManager(),
        Config(context_window=100, compression_threshold=0.5),
    )
    builder.history.append(Message("u", "user"))

    builder.record_prompt_tokens(50)
    assert builder.should_summarize() is False

    builder.record_prompt_tokens(51)
    assert builder.should_summarize() is True


def test_should_summarize_counts_unmeasured_tail():
    builder = ContextBuilder(
        HistoryManager(),
        Config(context_window=100, compression_threshold=0.5),
    )
    builder.history.append(Message("u", "user"))
    builder.record_prompt_tokens(10)
    builder.history.append(Message("x" * 400, "user"))

    assert builder.should_summarize() is True


def test_clear_resets_measured_tokens():
    builder = ContextBuilder(
        HistoryManager(),
        Config(context_window=100, compression_threshold=0.5),
    )
    builder.record_prompt_tokens(60)
    assert builder.should_summarize() is True

    builder.history.clear()

    assert builder.should_summarize() is False


def test_load_resets_measured_tokens():
    builder = ContextBuilder(
        HistoryManager(),
        Config(context_window=100, compression_threshold=0.5),
    )
    builder.record_prompt_tokens(60)
    assert builder.should_summarize() is True

    builder.history.load_from_dict({"history": [], "next_id": 0})

    assert builder.should_summarize() is False
