"""HistoryManager 只负责存储、id、轮次与序列化。"""
import pytest

from agents.context.history import HistoryManager
from agents.core.message import Message


# --------------------------------------------------------------------------- #
# 测试辅助
# --------------------------------------------------------------------------- #

def _manager_with(roles):
    manager = HistoryManager()
    for index, role in enumerate(roles):
        manager.append(Message(f"m{index}", role))
    return manager


# --------------------------------------------------------------------------- #
# 一、append / id
# --------------------------------------------------------------------------- #

def test_append_assigns_sequential_ids_from_zero():
    manager = HistoryManager()

    for index in range(3):
        message = manager.append(Message(f"m{index}", "user"))
        assert message.id == index

    assert [message.id for message in manager.get_history()] == [0, 1, 2]


def test_append_keeps_explicit_id_and_advances_counter():
    manager = HistoryManager()
    manager.append(Message("m0", "user"))

    message = manager.append(Message("m5", "assistant", id=5))
    following = manager.append(Message("m6", "user"))

    assert message.id == 5
    assert following.id == 6


def test_append_explicit_smaller_id_keeps_id_and_counter():
    manager = HistoryManager()
    for index in range(3):
        manager.append(Message(f"m{index}", "user"))

    message = manager.append(Message("old", "assistant", id=1))
    following = manager.append(Message("next", "user"))

    assert message.id == 1
    assert following.id == 3


def test_append_many_messages_keeps_strictly_increasing_ids():
    manager = HistoryManager()
    for index in range(100):
        manager.append(Message(f"m{index}", "user"))

    ids = [message.id for message in manager.get_history()]
    assert ids == list(range(100))


# --------------------------------------------------------------------------- #
# 二、轮次边界
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "roles, expected",
    [
        ([], 0),
        (["user"], 1),
        (["user", "assistant"], 1),
        (["user", "assistant", "user"], 2),
        (["assistant", "user", "tool"], 1),
        (["system", "user", "assistant", "user", "tool"], 2),
        (["user", "assistant", "tool", "summary"], 1),
    ],
)
def test_estimate_rounds(roles, expected):
    assert _manager_with(roles).estimate_rounds() == expected


def test_find_round_boundaries_returns_user_indexes():
    manager = _manager_with(["system", "user", "assistant", "user", "tool", "user"])

    assert manager.find_round_boundaries() == [1, 3, 5]


# --------------------------------------------------------------------------- #
# 三、读取与清空
# --------------------------------------------------------------------------- #

def test_get_history_returns_a_copy():
    manager = _manager_with(["user", "assistant"])

    history = manager.get_history()
    history.pop()

    assert len(manager.get_history()) == 2


def test_clear_removes_messages_but_keeps_id_counter_and_bumps_revision():
    manager = _manager_with(["user", "assistant"])
    revision = manager.revision

    manager.clear()

    assert manager.get_history() == []
    assert manager.revision == revision + 1
    assert manager.append(Message("next", "user")).id == 2


# --------------------------------------------------------------------------- #
# 四、序列化
# --------------------------------------------------------------------------- #

def test_to_dict_contains_history_next_id_rounds():
    manager = _manager_with(["user", "assistant", "user"])

    data = manager.to_dict()

    assert data["next_id"] == 3
    assert data["rounds"] == 2
    assert len(data["history"]) == 3
    assert data["history"][0]["content"] == "m0"


def test_load_from_dict_round_trip():
    manager = HistoryManager()
    manager.append(Message("u", "user"))
    manager.append(Message("a", "assistant"))
    data = manager.to_dict()

    restored = HistoryManager()
    restored.load_from_dict(data)

    assert [message.content for message in restored.get_history()] == ["u", "a"]
    assert [message.id for message in restored.get_history()] == [0, 1]
    assert restored.append(Message("next", "user")).id == 2


def test_load_from_dict_prefers_saved_next_id_when_greater():
    restored = HistoryManager()

    restored.load_from_dict({"history": [], "next_id": 42})

    assert restored.append(Message("next", "user")).id == 42


def test_load_from_dict_never_reuses_existing_ids():
    restored = HistoryManager()

    restored.load_from_dict(
        {"history": [{"id": 7, "role": "user", "content": "u"}], "next_id": 1}
    )

    assert restored.append(Message("next", "user")).id == 8


def test_load_from_dict_bumps_revision():
    manager = HistoryManager()
    revision = manager.revision

    manager.load_from_dict({"history": []})

    assert manager.revision == revision + 1


# --------------------------------------------------------------------------- #
# 五、契约边界
# --------------------------------------------------------------------------- #

def test_history_no_longer_owns_context_token_or_summary():
    manager = HistoryManager()

    for name in (
        "build_context",
        "build_snapshot",
        "estimate_message_tokens",
        "should_compress",
        "record_prompt_tokens",
        "add_summary",
        "summarize",
    ):
        assert not hasattr(manager, name)
