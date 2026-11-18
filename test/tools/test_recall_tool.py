"""Recall 工具：按 HistoryManager 消息列表下标召回完整消息或内容切片。"""
import pytest

from agents.context.history import HistoryManager
from agents.core.message import Message
from agents.tools.builtin.recall_tool import RecallTool
from agents.tools.response import ToolStatus


def _history(*messages) -> HistoryManager:
    history = HistoryManager()
    for message in messages:
        history.append(message)
    return history


def _tool(*messages) -> RecallTool:
    return RecallTool(_history(*messages))


def test_recall_full_message_by_index():
    tool = _tool(Message("hello world", "user"), Message("answer", "assistant"))

    response = tool.run({"id": 1})

    assert response.status is ToolStatus.SUCCESS
    assert "answer" in response.text
    assert "assistant" in response.text
    assert "1" in response.text


def test_recall_returns_content_slice_when_offset_and_length_given():
    tool = _tool(Message("0123456789", "user"))

    response = tool.run({"id": 0, "offset": 2, "length": 3})

    assert response.status is ToolStatus.SUCCESS
    assert "234" in response.text
    assert "0123456789" not in response.text


def test_recall_falls_back_to_full_message_when_offset_missing():
    tool = _tool(Message("abcdef", "user"))

    response = tool.run({"id": 0, "length": 2})

    assert response.status is ToolStatus.SUCCESS
    assert "abcdef" in response.text


def test_recall_falls_back_to_full_message_when_length_missing():
    tool = _tool(Message("abcdef", "user"))

    response = tool.run({"id": 0, "offset": 2})

    assert response.status is ToolStatus.SUCCESS
    assert "abcdef" in response.text


def test_recall_rejects_unknown_index():
    tool = _tool(Message("only", "user"))

    response = tool.run({"id": 5})

    assert response.status is ToolStatus.ERROR
    assert response.error_info["code"] == "NOT_FOUND"
    assert "5" in response.text


def test_recall_rejects_negative_index():
    tool = _tool(Message("only", "user"))

    response = tool.run({"id": -1})

    assert response.status is ToolStatus.ERROR
    assert response.error_info["code"] == "NOT_FOUND"


def test_recall_rejects_missing_index():
    tool = _tool(Message("only", "user"))

    response = tool.run({})

    assert response.status is ToolStatus.ERROR
    assert response.error_info["code"] == "INVALID_PARAM"


def test_recall_rejects_non_integer_index():
    tool = _tool(Message("only", "user"))

    response = tool.run({"id": "abc"})

    assert response.status is ToolStatus.ERROR
    assert response.error_info["code"] == "INVALID_PARAM"


def test_recall_slice_past_end_is_truncated():
    tool = _tool(Message("abcdef", "user"))

    response = tool.run({"id": 0, "offset": 4, "length": 100})

    assert response.status is ToolStatus.SUCCESS
    assert "ef" in response.text


def test_recall_offset_beyond_content_returns_empty_slice():
    tool = _tool(Message("abcdef", "user"))

    response = tool.run({"id": 0, "offset": 100, "length": 5})

    assert response.status is ToolStatus.SUCCESS
    assert "abc" not in response.text


def test_recall_reflects_messages_appended_after_construction():
    history = _history(Message("first", "user"))
    tool = RecallTool(history)
    history.append(Message("second", "assistant"))

    response = tool.run({"id": 1})

    assert response.status is ToolStatus.SUCCESS
    assert "second" in response.text
