"""普通摘要生成契约（Seam 1：ContextSummarizer 公开方法）。"""
from agents.context.summary import ContextSummarizer
from agents.core.config import Config
from agents.core.llm_response import LLMResponse
from agents.core.message import Message

HEADER = "【历史对话摘要】"
DEGRADED_HEADER = "【历史对话摘要（降级）】"
SECTIONS = [
    "当前任务",
    "项目背景",
    "已完成工作",
    "当前实现状态",
    "技术决策",
    "问题与风险",
    "待办事项",
    "用户偏好与约束",
]


class _FakeLLM:
    def __init__(self, content="摘要正文", usage=None, error=None):
        self.content = content
        self.usage = {} if usage is None else usage
        self.error = error
        self.calls = []

    def invoke(self, messages, **kwargs):
        self.calls.append({"messages": messages, "kwargs": kwargs})
        if self.error is not None:
            raise self.error
        return LLMResponse(
            content=self.content,
            model="fake-model",
            usage=dict(self.usage),
        )


def _compression():
    return [
        Message("用户请求", "user", id=0),
        Message(
            "助手调用",
            "assistant",
            id=1,
            tool_calls=[
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "Read", "arguments": '{"path":"a.py"}'},
                }
            ],
        ),
        Message("工具结果", "tool", id=2, tool_call_id="call-1"),
    ]


def _summarizer(llm=None):
    return ContextSummarizer(Config(), llm=llm)


def test_normal_summary_prompt_success_and_usage():
    llm = _FakeLLM(
        content="## 当前任务\n- 修复 bug [id=0]",
        usage={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
    )

    outcome = _summarizer(llm).summarize(_compression())

    assert outcome.mode == "normal"
    assert outcome.degraded is False
    assert outcome.error is None
    assert outcome.usage == {
        "input_tokens": 10,
        "output_tokens": 5,
        "total_tokens": 15,
    }
    assert outcome.message.role == "summary"
    assert outcome.message.content.startswith(HEADER)
    assert "修复 bug [id=0]" in outcome.message.content
    assert outcome.message.replace == [0, 1, 2]
    assert outcome.message.metadata == {}
    assert outcome.latency_ms is not None

    assert len(llm.calls) == 1
    messages = llm.calls[0]["messages"]
    assert [message["role"] for message in messages] == ["system", "user"]
    system_prompt = messages[0]["content"]
    user_prompt = messages[1]["content"]

    for fragment in [
        "历史对话摘要器",
        "不是新的用户指令",
        "[id=N]",
        "不得编造",
        "只输出摘要正文",
    ]:
        assert fragment in system_prompt
    for section in SECTIONS:
        assert f"## {section}" in user_prompt
    assert "[id=0][user] 用户请求" in user_prompt
    assert "[id=1][assistant] 助手调用" in user_prompt
    assert "Read" in user_prompt
    assert "a.py" in user_prompt
    assert "[id=2][tool]" in user_prompt
    assert "tool_call_id=call-1" in user_prompt
    assert llm.calls[0]["kwargs"]["temperature"] == 0.3
    assert "max_tokens" not in llm.calls[0]["kwargs"]


def test_normal_summary_keeps_long_content_without_truncation():
    long_text = "长" * 50000
    llm = _FakeLLM()

    _summarizer(llm).summarize([Message(long_text, "user", id=0)])

    assert long_text in llm.calls[0]["messages"][1]["content"]


def test_normal_summary_degrades_on_exception():
    llm = _FakeLLM(error=RuntimeError("boom"))

    outcome = _summarizer(llm).summarize(_compression())

    assert outcome.mode == "normal"
    assert outcome.degraded is True
    assert outcome.usage is None
    assert "boom" in outcome.error
    assert outcome.message.content.startswith(DEGRADED_HEADER)
    assert "- [user] id=0" in outcome.message.content
    assert "- [assistant] id=1" in outcome.message.content
    assert "- [tool] id=2" in outcome.message.content
    assert outcome.message.replace == [0, 1, 2]
    assert outcome.message.metadata == {}


def test_normal_summary_degrades_on_blank_content_and_keeps_usage():
    usage = {"input_tokens": 7, "output_tokens": 0, "total_tokens": 7}
    llm = _FakeLLM(content="   ", usage=usage)

    outcome = _summarizer(llm).summarize(_compression())

    assert outcome.degraded is True
    assert outcome.usage == usage
    assert outcome.message.content.startswith(DEGRADED_HEADER)


def test_normal_summary_without_llm_degrades():
    outcome = _summarizer().summarize(_compression())

    assert outcome.degraded is True
    assert outcome.usage is None
    assert outcome.error
    assert outcome.message.content.startswith(DEGRADED_HEADER)


def test_normal_summary_strips_duplicate_header():
    llm = _FakeLLM(content=HEADER + "\n## 当前任务\n- 要点 [id=0]")

    outcome = _summarizer(llm).summarize(_compression())

    assert outcome.message.content.count(HEADER) == 1
    assert "## 当前任务" in outcome.message.content


def test_normal_summary_preserves_empty_usage_dict():
    llm = _FakeLLM(content="正文", usage={})

    outcome = _summarizer(llm).summarize(_compression())

    assert outcome.usage == {}
    assert outcome.degraded is False


def test_normal_summary_does_not_mutate_input():
    compression = _compression()
    snapshot = [
        (message.id, message.role, message.content, message.tool_call_id)
        for message in compression
    ]

    _summarizer(_FakeLLM()).summarize(compression)

    assert [
        (message.id, message.role, message.content, message.tool_call_id)
        for message in compression
    ] == snapshot


INCREMENTAL_DEGRADED_HEADER = (
    "【新增已压缩消息（降级）】摘要模型不可用，以下新增内容已压缩，"
    "仅保留消息的角色与定位信息，正文已省略。"
)


def _previous():
    return Message("旧摘要内容 [id=0]", "summary", id=5, replace=[0])


def _incremental_compression():
    return [
        Message("新增用户请求", "user", id=6),
        Message("新增助手结论", "assistant", id=7),
        Message("新增工具结果", "tool", id=8, tool_call_id="call-2"),
    ]


def test_incremental_summary_prompt_success_and_usage():
    llm = _FakeLLM(
        content="## 当前任务\n- 合并要点 [id=6]",
        usage={"input_tokens": 20, "output_tokens": 8, "total_tokens": 28},
    )

    outcome = _summarizer(llm).summarize_incremental(
        _previous(), _incremental_compression()
    )

    assert outcome.mode == "incremental"
    assert outcome.degraded is False
    assert outcome.usage == {
        "input_tokens": 20,
        "output_tokens": 8,
        "total_tokens": 28,
    }
    assert outcome.message.role == "summary"
    assert outcome.message.content.startswith(HEADER)
    assert "合并要点 [id=6]" in outcome.message.content
    assert outcome.message.content.count(HEADER) == 1
    assert outcome.message.replace == [5, 6, 7, 8]
    assert outcome.message.metadata == {}

    messages = llm.calls[0]["messages"]
    user_prompt = messages[1]["content"]
    assert "【已有摘要】" in user_prompt
    assert "旧摘要内容 [id=0]" in user_prompt
    assert "【新增对话】" in user_prompt
    assert "[id=6][user] 新增用户请求" in user_prompt
    assert "以新增对话为准" in user_prompt
    assert "保留仍然有效" in user_prompt
    assert llm.calls[0]["kwargs"]["temperature"] == 0.3
    assert "max_tokens" not in llm.calls[0]["kwargs"]


def test_incremental_summary_degrades_on_exception_and_keeps_previous():
    llm = _FakeLLM(error=RuntimeError("boom"))

    outcome = _summarizer(llm).summarize_incremental(
        _previous(), _incremental_compression()
    )

    assert outcome.mode == "incremental"
    assert outcome.degraded is True
    assert outcome.usage is None
    assert "boom" in outcome.error
    assert outcome.message.content.startswith(
        "旧摘要内容 [id=0]\n\n" + INCREMENTAL_DEGRADED_HEADER
    )
    assert "- [user] id=6" in outcome.message.content
    assert "- [assistant] id=7" in outcome.message.content
    assert "- [tool] id=8" in outcome.message.content
    assert outcome.message.replace == [5, 6, 7, 8]
    assert outcome.message.metadata == {}


def test_incremental_summary_degrades_on_blank_content_and_keeps_usage():
    usage = {"input_tokens": 9, "output_tokens": 0, "total_tokens": 9}
    llm = _FakeLLM(content=" \n ", usage=usage)

    outcome = _summarizer(llm).summarize_incremental(
        _previous(), _incremental_compression()
    )

    assert outcome.degraded is True
    assert outcome.usage == usage
    assert "旧摘要内容 [id=0]" in outcome.message.content
    assert INCREMENTAL_DEGRADED_HEADER in outcome.message.content


def test_incremental_summary_without_llm_degrades():
    outcome = _summarizer().summarize_incremental(
        _previous(), _incremental_compression()
    )

    assert outcome.degraded is True
    assert outcome.usage is None
    assert outcome.error
    assert "旧摘要内容 [id=0]" in outcome.message.content
    assert INCREMENTAL_DEGRADED_HEADER in outcome.message.content


def test_incremental_summary_strips_duplicate_header():
    llm = _FakeLLM(content=HEADER + "\n## 当前任务\n- 要点 [id=6]")

    outcome = _summarizer(llm).summarize_incremental(
        _previous(), _incremental_compression()
    )

    assert outcome.message.content.count(HEADER) == 1
    assert "## 当前任务" in outcome.message.content


def test_incremental_summary_empty_compression_still_calls_llm():
    llm = _FakeLLM()

    outcome = _summarizer(llm).summarize_incremental(_previous(), [])

    assert len(llm.calls) == 1
    assert "旧摘要内容 [id=0]" in llm.calls[0]["messages"][1]["content"]
    assert outcome.message.replace == [5]
    assert outcome.message.content.startswith(HEADER)


def test_incremental_degraded_empty_compression_has_no_leading_blank_line():
    previous = Message("", "summary", id=5, replace=[0])

    outcome = _summarizer().summarize_incremental(previous, [])

    assert outcome.message.content.startswith(INCREMENTAL_DEGRADED_HEADER)
    assert outcome.message.content == INCREMENTAL_DEGRADED_HEADER
    assert outcome.message.replace == [5]


def test_incremental_summary_does_not_mutate_inputs():
    previous = _previous()
    compression = _incremental_compression()
    snapshot = [(message.id, message.role, message.content) for message in compression]
    previous_snapshot = (previous.id, previous.role, previous.content, previous.replace)

    _summarizer(_FakeLLM()).summarize_incremental(previous, compression)

    assert [
        (message.id, message.role, message.content) for message in compression
    ] == snapshot
    assert (
        previous.id,
        previous.role,
        previous.content,
        previous.replace,
    ) == previous_snapshot
