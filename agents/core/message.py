from pydantic import BaseModel, Field

from typing import List,Literal, Optional, Dict,Any
from datetime import datetime

MessageRole = Literal['user','assistant','system','tool','summary']

class Message(BaseModel):
    # 历史记录中的唯一标识, 由 HistoryManager 按追加顺序分配 (从 0 开始)
    id: Optional[int] = None
    content: str
    role: MessageRole
    # 与 code_agent 循环中的 message 对齐: role='tool' 时指向对应 tool_call 的 id
    tool_call_id: Optional[str] = None
    # 与 code_agent 循环中的 message 对齐: role='assistant' 时的工具调用列表
    tool_calls: Optional[List[Dict[str, Any]]] = None
    # 仅 role='summary' 时非空: 记录该摘要遮蔽了哪些原始消息 (被遮蔽消息的 id 列表)
    replace: List[int] = Field(default_factory=list)
    timestamp: Optional[datetime] = None
    metadata: Optional[Dict[str, Any]] = None

    def __init__(self, content: str, role: MessageRole, **kwargs):
        super().__init__(
            id=kwargs.get("id"),
            content=content,
            role=role,
            tool_call_id=kwargs.get("tool_call_id"),
            tool_calls=kwargs.get("tool_calls"),
            replace=kwargs.get("replace") or [],
            timestamp=kwargs.get("timestamp", datetime.now()),
            metadata=kwargs.get("metadata", {})
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            'id': self.id,
            'content': self.content,
            'role': self.role,
            'tool_call_id': self.tool_call_id,
            'tool_calls': self.tool_calls,
            'replace': list(self.replace or []),
            'timestamp': self.timestamp.isoformat() if self.timestamp else None,
            'metadata': self.metadata
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Message':
        """ 从字典创建Message对象 """
        # 把iso字符格式的时间转为datetime
        timestamp = data.get("timestamp")
        if timestamp and isinstance(timestamp, str):
            timestamp = datetime.fromisoformat(timestamp)
        return cls(
            content = data.get('content') or "",
            role = data['role'],
            id = data.get('id'),
            tool_call_id = data.get('tool_call_id'),
            tool_calls = data.get('tool_calls'),
            replace = data.get('replace') or [],
            timestamp = timestamp,
            metadata = data.get('metadata') or {}
        )

    @classmethod
    def from_llm_dict(cls, data: Dict[str, Any]) -> 'Message':
        """ 从"发给大模型的消息"字典创建Message对象 """
        return cls(
            content = data.get('content') or "",
            role = data['role'],
            tool_call_id = data.get('tool_call_id'),
            tool_calls = data.get('tool_calls')
        )

    def to_llm_dict(self) -> Dict[str, Any]:
        """ 转为"发给大模型的消息字典"

        summary 不是合法的 API 角色，发送时降级为 user（内容即摘要正文）。
        """
        role = "user" if self.role == "summary" else self.role
        payload: Dict[str, Any] = {'role': role, 'content': self.content}
        if self.role == 'assistant' and self.tool_calls:
            payload['tool_calls'] = self.tool_calls
        if self.role == 'tool' and self.tool_call_id:
            payload['tool_call_id'] = self.tool_call_id
        return payload

    def to_text(self):
        """ message对象转为文本 """
        return f"[{self.role}] {self.content}"

    def __str__(self) -> str:
        return f"[{self.role}] {self.content}"


if __name__ == '__main__':
    m = Message.from_dict({
        'role': 'user',
        'content': 'hello world',
        'timestamp': datetime.fromisoformat('2019-02-01 01:02:03'),
        'metadata': {}
    })
    print(m.to_text())





