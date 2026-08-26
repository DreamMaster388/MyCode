from pydantic import BaseModel

from typing import List,Literal, Optional, Dict,Any
from datetime import datetime

MessageRole = Literal['user','assistant','system','tool','summary']

class Message(BaseModel):
    content: str
    role: MessageRole
    timestamp: datetime = None
    metadata: Optional[Dict[str, Any]] = None
    def __init__(self, content: str, role: MessageRole, **kwargs):
        super().__init__(
            content=content,
            role=role,
            timestamp=kwargs.get("timestamp", datetime.now()),
            metadata=kwargs.get("metadata", {})
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            'content': self.content,
            'role': self.role,
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
            content = data['content'],
            role = data['role'],
            timestamp = timestamp,
            metadata = data['metadata']
        )

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





