"""HistoryManager 关键函数的单元测试（对应 context-design/05-测试）。

覆盖范围（与计划一一对应）：
- append / estimate_rounds / build_snapshot / build_context
- should_compress / add_summary
- summarize / _build_summary_prompt / _simple_summary / _get_summary_llm

运行方式（见 README）：python -m pytest test/
"""
from types import SimpleNamespace

import pytest

from agents.context.history import HistoryManager, NotEnoughRoundsError
from agents.core.config import Config
from agents.core.llm_response import LLMResponse
from agents.core.message import Message


# --------------------------------------------------------------------------- #
# 测试辅助
# --------------------------------------------------------------------------- #

def _add_round(hm: HistoryManager, index: int) -> None:
    """追加一轮完整对话（user + assistant）。"""
    hm.append(Message(f"u{index}", "user"))
    hm.append(Message(f"a{index}", "assistant"))


def _compressible_manager(min_retain_rounds: int = 2) -> HistoryManager:
    """构造一个"必然需要压缩"的 HistoryManager。

    context_window=1 且 threshold=0 时，只要有非空消息，should_compress() 就为 True。
    """
    return HistoryManager(
        min_retain_rounds=min_retain_rounds,
        compression_threshold=0.0,
        context_window=1,
        config=Config(enable_smart_compression=True),
    )


class _FakeSummaryLLM:
    """记录调用参数的假摘要 LLM。"""

    def __init__(self, text: str = "摘要正文", fail: bool = False, empty: bool = False):
        self.text = text
        self.fail = fail
        self.empty = empty
        self.calls = []

    def invoke(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        if self.fail:
            raise RuntimeError("boom")
        return LLMResponse(content="" if self.empty else self.text, model="fake-summary")


@pytest.fixture
def fake_summary_llm_class(monkeypatch):
    """把 HelloAgentsLLM 替换为记录型假类，避免真实构造/网络调用。"""

    class _RecorderLLM:
        instances = []

        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.model = kwargs.get("model")
            _RecorderLLM.instances.append(self)

    monkeypatch.setattr("agents.core.llm.HelloAgentsLLM", _RecorderLLM)
    return _RecorderLLM


# --------------------------------------------------------------------------- #
# 一、append
# --------------------------------------------------------------------------- #

def test_append_assigns_sequential_ids_from_zero():
    hm = HistoryManager()

    first = hm.append(Message("u0", "user"))
    second = hm.append(Message("a0", "assistant"))
    third = hm.append(Message("u1", "user"))

    assert [m.id for m in (first, second, third)] == [0, 1, 2]
    assert hm.get_history() == [first, second, third]


def test_append_keeps_explicit_id_and_advances_counter():
    hm = HistoryManager()

    provided = Message("a0", "assistant", id=7)
    returned = hm.append(provided)

    assert returned is provided
    assert provided.id == 7
    # 游标被推进到显式 id 之后
    assert hm.append(Message("u0", "user")).id == 8


def test_append_explicit_smaller_id_keeps_id_and_counter():
    hm = HistoryManager()
    hm.append(Message("u0", "user"))       # id=0 -> next=1
    hm.append(Message("a0", "assistant"))  # id=1 -> next=2

    small = Message("u1", "user", id=0)
    hm.append(small)

    assert small.id == 0
    # 保留原 id，且不会让后续分配重复
    assert hm.append(Message("a1", "assistant")).id == 2


def test_append_many_messages_keeps_strictly_increasing_ids():
    hm = HistoryManager()
    for i in range(10):
        hm.append(Message(f"m{i}", "user"))

    assert [m.id for m in hm.get_history()] == list(range(10))
    assert hm.build_snapshot() == hm.get_history()


# --------------------------------------------------------------------------- #
# 二、estimate_rounds
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "roles, expected",
    [
        ([], 0),
        (["assistant", "tool"], 0),
        (["user"], 1),
        (["user", "assistant"], 1),
        (["user", "assistant", "user", "assistant"], 2),
        (["user", "assistant", "tool", "assistant", "user", "assistant"], 2),
        # user 之前的 system/assistant 不计轮次
        (["system", "assistant", "user", "assistant"], 1),
        # 连续 user 视为两轮
        (["user", "user"], 2),
        # summary 不是 user，不单独计轮
        (["summary", "user", "assistant"], 1),
        (["user", "assistant", "summary", "user"], 2),
    ],
)
def test_estimate_rounds(roles, expected):
    hm = HistoryManager()
    for i, role in enumerate(roles):
        hm.append(Message(f"m{i}", role))

    assert hm.estimate_rounds() == expected


# --------------------------------------------------------------------------- #
# 三、build_snapshot
# --------------------------------------------------------------------------- #

def test_snapshot_without_summary_returns_all_messages_ordered():
    hm = HistoryManager()
    for i in range(4):
        hm.append(Message(f"m{i}", "user"))

    snapshot = hm.build_snapshot()

    assert [m.content for m in snapshot] == ["m0", "m1", "m2", "m3"]
    # 返回新列表，修改它不影响历史
    assert snapshot is not hm.get_history()
    snapshot.pop()
    assert len(hm.get_history()) == 4


def test_snapshot_replaces_closed_range_and_keeps_tail():
    hm = HistoryManager()
    for i in range(5):
        hm.append(Message(f"m{i}", "user"))
    hm.append(Message("SUM", "summary", replace=[0, 3]))

    assert [m.content for m in hm.build_snapshot()] == ["SUM", "m4"]


def test_snapshot_replaces_only_the_bounded_segment():
    hm = HistoryManager()
    for i in range(5):
        hm.append(Message(f"m{i}", "user"))
    hm.append(Message("SUM", "summary", replace=[1, 3]))

    # 只遮蔽 m1..m3，m0 与 m4 保留
    assert [m.content for m in hm.build_snapshot()] == ["m0", "SUM", "m4"]


def test_snapshot_supports_inverted_replace_range():
    hm = HistoryManager()
    for i in range(3):
        hm.append(Message(f"m{i}", "user"))
    # S1(id=3) 遮蔽 m0/m1 后位于 m2 之前；下一次前缀 replace 为 [3, 2]（逆序）
    hm.append(Message("S1", "summary", replace=[0, 1]))
    hm.append(Message("S2", "summary", replace=[3, 2]))

    assert [m.content for m in hm.build_snapshot()] == ["S2"]


def test_snapshot_handles_multiple_non_overlapping_summaries():
    hm = HistoryManager()
    for i in range(4):
        hm.append(Message(f"m{i}", "user"))
    hm.append(Message("S1", "summary", replace=[0, 1]))
    hm.append(Message("S2", "summary", replace=[2, 3]))

    assert [m.content for m in hm.build_snapshot()] == ["S1", "S2"]


def test_snapshot_keeps_summary_without_replace_as_plain_message():
    hm = HistoryManager()
    hm.append(Message("u", "user"))
    hm.append(Message("S", "summary"))  # replace 为空，不参与遮蔽

    assert [m.content for m in hm.build_snapshot()] == ["u", "S"]


def test_snapshot_survives_shadowed_endpoints():
    hm = HistoryManager()
    for i in range(3):
        hm.append(Message(f"m{i}", "user"))
    hm.append(Message("S1", "summary", replace=[0, 2]))
    # 端点 0/1 已被 S1 遮蔽：走兜底分支，不抛异常、原摘要仍在
    hm.append(Message("S2", "summary", replace=[0, 1]))

    contents = [m.content for m in hm.build_snapshot()]
    assert "S1" in contents
    assert contents[-1] == "S2"


# --------------------------------------------------------------------------- #
# 四、build_context
# --------------------------------------------------------------------------- #

def test_build_context_with_system_prompt():
    hm = HistoryManager()
    hm.append(Message("u0", "user"))
    hm.append(Message("a0", "assistant"))

    assert hm.build_context("SYS") == [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "u0"},
        {"role": "assistant", "content": "a0"},
    ]


@pytest.mark.parametrize("system_prompt", [None, ""])
def test_build_context_without_system_prompt(system_prompt):
    hm = HistoryManager()
    hm.append(Message("u0", "user"))

    context = hm.build_context(system_prompt)

    assert context == [{"role": "user", "content": "u0"}]


def test_build_context_preserves_tool_calls_and_tool_call_id():
    hm = HistoryManager()
    hm.append(Message("u0", "user"))
    hm.append(Message(
        "",
        "assistant",
        tool_calls=[{
            "id": "call_1",
            "type": "function",
            "function": {"name": "Read", "arguments": "{}"},
        }],
    ))
    hm.append(Message("file content", "tool", tool_call_id="call_1"))

    context = hm.build_context()

    assert context[1]["tool_calls"][0]["id"] == "call_1"
    assert context[2]["tool_call_id"] == "call_1"


def test_build_context_applies_summary_and_maps_summary_to_user_role():
    hm = HistoryManager()
    hm.append(Message("u0", "user"))
    hm.append(Message("a0", "assistant"))
    hm.append(Message("u1", "user"))
    hm.append(Message("a1", "assistant"))
    hm.append(Message("SUM", "summary", replace=[0, 3]))

    context = hm.build_context("SYS")

    # summary 不是合法 API role，发送时应降级为 user
    assert [m["role"] for m in context] == ["system", "user"]
    assert context[1] == {"role": "user", "content": "SUM"}


def test_build_context_returns_new_structure_each_call():
    hm = HistoryManager()
    hm.append(Message("u0", "user"))

    first = hm.build_context("SYS")
    first.append({"role": "user", "content": "mutated"})

    assert hm.build_context("SYS") == [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "u0"},
    ]


# --------------------------------------------------------------------------- #
# 五、should_compress
# --------------------------------------------------------------------------- #

def test_should_compress_uses_window_times_threshold_strictly():
    # 窗口 100 * 阈值 0.5 = 50
    message = Message("m", "user")

    # 恰好等于阈值：不压缩
    hm_at = HistoryManager(context_window=100, compression_threshold=0.5)
    estimate = hm_at.estimate_message_tokens(message)
    hm_at.record_prompt_tokens(50 - estimate)  # 已计量 0 条，尾部消息靠估算
    hm_at.append(message)
    assert hm_at.should_compress() is False

    # 超过阈值 1 token：压缩
    hm_over = HistoryManager(context_window=100, compression_threshold=0.5)
    hm_over.record_prompt_tokens(51 - estimate)
    hm_over.append(message)
    assert hm_over.should_compress() is True


def test_should_compress_counts_only_unmeasured_tail():
    hm = HistoryManager(context_window=100, compression_threshold=0.5)
    for i in range(3):
        hm.append(Message("x" * 40, "user"))

    hm.record_prompt_tokens(10)  # 已计量 [0, 3)
    assert hm.should_compress() is False

    tail = Message("y" * 400, "user")
    hm.append(tail)  # strlen≈407 -> 101 tokens
    assert hm.estimate_message_tokens(tail) == 101
    assert hm.should_compress() is True  # 10 + 101 > 50


def test_should_compress_false_for_empty_history():
    hm = HistoryManager(context_window=100, compression_threshold=0.5)
    assert hm.should_compress() is False


# --------------------------------------------------------------------------- #
# 六、add_summary
# --------------------------------------------------------------------------- #

def test_add_summary_returns_none_when_compression_not_needed():
    hm = HistoryManager()  # 默认 128k 窗口，小历史不会触发
    for i in range(4):
        _add_round(hm, i)
    fake = _FakeSummaryLLM()
    hm._summary_llm = fake

    assert hm.add_summary() is None
    assert fake.calls == []
    assert all(m.role != "summary" for m in hm.get_history())


@pytest.mark.parametrize(
    "min_retain_rounds, rounds",
    [
        (2, 2),  # 保留轮数 == 当前轮数
        (3, 2),  # 保留轮数 > 当前轮数
    ],
)
def test_add_summary_raises_when_retain_rounds_not_less_than_rounds(min_retain_rounds, rounds):
    hm = _compressible_manager(min_retain_rounds=min_retain_rounds)
    for i in range(rounds):
        _add_round(hm, i)

    with pytest.raises(NotEnoughRoundsError):
        hm.add_summary()


def test_add_summary_compresses_only_older_rounds():
    hm = _compressible_manager(min_retain_rounds=2)
    for i in range(3):
        _add_round(hm, i)
    hm._summary_llm = _FakeSummaryLLM("SUM")

    summary = hm.add_summary()

    # 3 轮保留最近 2 轮 -> 压缩 u0/a0（id 0..1）
    assert summary is not None
    assert summary.role == "summary"
    assert summary.content == "SUM"
    assert summary.replace == [0, 1]
    assert hm.get_history()[-1] is summary
    assert [m.content for m in hm.build_snapshot()] == ["SUM", "u1", "a1", "u2", "a2"]


def test_add_summary_passes_only_prefix_to_summarize():
    hm = _compressible_manager(min_retain_rounds=2)
    for i in range(3):
        _add_round(hm, i)
    fake = _FakeSummaryLLM("SUM")
    hm._summary_llm = fake

    hm.add_summary()

    prompt = fake.calls[0][0][1]["content"]
    assert "[user] u0" in prompt and "[assistant] a0" in prompt
    assert "u1" not in prompt and "a1" not in prompt


def test_add_summary_detects_no_new_compressible_content():
    hm = _compressible_manager(min_retain_rounds=2)
    for i in range(4):
        _add_round(hm, i)
    fake = _FakeSummaryLLM("SUM")
    hm._summary_llm = fake

    first = hm.add_summary()
    assert first is not None
    calls_after_first = len(fake.calls)

    # 前缀只剩刚生成的摘要，没有新增可压缩内容
    assert hm.add_summary() is None
    assert len(fake.calls) == calls_after_first
    assert sum(1 for m in hm.get_history() if m.role == "summary") == 1


def test_add_summary_progresses_after_new_round():
    hm = _compressible_manager(min_retain_rounds=2)
    for i in range(4):
        _add_round(hm, i)
    hm._summary_llm = _FakeSummaryLLM("S1")

    first = hm.add_summary()
    assert first is not None and first.replace == [0, 3]

    _add_round(hm, 4)
    hm._summary_llm.text = "S2"
    second = hm.add_summary()

    # 第二次压缩 [S1, u2, a2]，replace 首尾来自快照（首元素是 S1，id 更大 -> 逆序）
    assert second is not None
    assert second.replace == [first.id, 5]
    assert [m.content for m in hm.build_snapshot()] == ["S2", "u3", "a3", "u4", "a4"]


def test_add_summary_detects_overlap_between_retained_round_and_summary():
    hm = _compressible_manager(min_retain_rounds=1)
    for i in range(3):
        _add_round(hm, i)  # id 0..5
    # 摘要覆盖 u2(id=4)，而保留最近 1 轮恰好是 u2
    hm.append(Message("S", "summary", replace=[0, 4]))
    fake = _FakeSummaryLLM()
    hm._summary_llm = fake

    assert hm.add_summary() is None
    assert fake.calls == []


# --------------------------------------------------------------------------- #
# 七、summarize
# --------------------------------------------------------------------------- #

def test_summarize_empty_messages_returns_empty_string():
    hm = HistoryManager(config=Config(enable_smart_compression=True))
    assert hm.summarize([]) == ""


def test_summarize_falls_back_to_simple_summary_without_config():
    hm = HistoryManager()
    text = hm.summarize([Message("u", "user"), Message("a", "assistant")])

    assert "此会话包含 1 轮对话" in text
    assert "- 总消息数：2 条" in text


def test_summarize_falls_back_when_smart_compression_disabled():
    hm = HistoryManager(config=Config(enable_smart_compression=False))
    fake = _FakeSummaryLLM("不应被调用")
    hm._summary_llm = fake

    text = hm.summarize([Message("u", "user")])

    assert "此会话包含 1 轮对话" in text
    assert fake.calls == []


def test_summarize_uses_model_text_and_passes_config():
    config = Config(
        enable_smart_compression=True,
        summary_temperature=0.1,
        summary_max_tokens=123,
    )
    hm = HistoryManager(config=config)
    fake = _FakeSummaryLLM("模型摘要")
    hm._summary_llm = fake

    text = hm.summarize([Message("u0", "user"), Message("a0", "assistant")])

    assert text == "模型摘要"
    messages, kwargs = fake.calls[0]
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    assert messages[1]["content"].startswith("请把下面的对话历史")
    assert kwargs == {"temperature": 0.1, "max_tokens": 123}


def test_summarize_falls_back_when_model_returns_empty():
    hm = HistoryManager(config=Config(enable_smart_compression=True))
    hm._summary_llm = _FakeSummaryLLM(empty=True)

    text = hm.summarize([Message("u", "user")])

    assert "此会话包含 1 轮对话" in text


def test_summarize_falls_back_when_model_raises(capsys):
    hm = HistoryManager(config=Config(enable_smart_compression=True))
    hm._summary_llm = _FakeSummaryLLM(fail=True)

    text = hm.summarize([Message("u", "user")])

    assert "此会话包含 1 轮对话" in text
    assert "降级为简单摘要" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# 八、_build_summary_prompt
# --------------------------------------------------------------------------- #

def test_build_summary_prompt_joins_messages_and_truncates_long_content():
    hm = HistoryManager()
    limit = HistoryManager._SUMMARY_CONTENT_LIMIT
    long_content = "x" * (limit + 20)

    prompt = hm._build_summary_prompt([
        Message("短消息", "user"),
        Message(long_content, "tool"),
    ])

    assert "[user] 短消息" in prompt
    assert f"[tool] {'x' * limit}...(已截断)" in prompt
    assert "## 对话历史" in prompt
    assert prompt.endswith("只输出摘要正文，不要加标题或额外说明。")
    # 超出上限的部分不应进入 prompt
    assert "x" * (limit + 1) not in prompt


# --------------------------------------------------------------------------- #
# 九、_simple_summary
# --------------------------------------------------------------------------- #

def test_simple_summary_counts_each_role():
    messages = [
        Message("u0", "user"),
        Message("a0", "assistant"),
        Message("t0", "tool"),
        Message("a1", "assistant"),
        Message("u1", "user"),
        Message("S", "summary"),
    ]

    text = HistoryManager._simple_summary(messages)

    assert text == (
        "此会话包含 2 轮对话：\n"
        "- 用户消息：2 条\n"
        "- 助手消息：2 条\n"
        "- 工具消息：1 条\n"
        "- 总消息数：6 条"
    )


# --------------------------------------------------------------------------- #
# 十、_get_summary_llm
# --------------------------------------------------------------------------- #

def test_get_summary_llm_prefers_config_and_caches(fake_summary_llm_class):
    config = Config(
        enable_smart_compression=True,
        summary_llm_model="sum-model",
        summary_llm_api_key="sum-key",
        summary_llm_base_url="https://sum.example",
        summary_temperature=0.2,
        summary_max_tokens=321,
    )
    main_llm = SimpleNamespace(model="main-model", api_key="main-key", base_url="https://main.example")
    hm = HistoryManager(config=config, llm=main_llm)

    first = hm._get_summary_llm()
    second = hm._get_summary_llm()

    assert first is second
    assert len(fake_summary_llm_class.instances) == 1
    assert first.kwargs == {
        "model": "sum-model",
        "api_key": "sum-key",
        "base_url": "https://sum.example",
        "temperature": 0.2,
        "max_tokens": 321,
    }


def test_get_summary_llm_falls_back_to_main_llm(fake_summary_llm_class):
    config = Config(
        enable_smart_compression=True,
        summary_llm_model="",
        summary_llm_api_key=None,
        summary_llm_base_url=None,
    )
    main_llm = SimpleNamespace(model="main-model", api_key="main-key", base_url="https://main.example")
    hm = HistoryManager(config=config, llm=main_llm)

    llm = hm._get_summary_llm()

    assert llm.kwargs["model"] == "main-model"
    assert llm.kwargs["api_key"] == "main-key"
    assert llm.kwargs["base_url"] == "https://main.example"


def test_get_summary_llm_without_config_uses_main_llm(fake_summary_llm_class):
    main_llm = SimpleNamespace(model="main-model", api_key="main-key", base_url="https://main.example")
    hm = HistoryManager(config=None, llm=main_llm)

    llm = hm._get_summary_llm()

    assert llm.kwargs["model"] == "main-model"
    assert llm.kwargs["api_key"] == "main-key"
    assert llm.kwargs["base_url"] == "https://main.example"
