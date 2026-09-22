from ..base import Command
from ...core.mode import ModeGuard, AgentMode

class ModeCommand(Command):
    def __init__(self, name: str = "mode", description: str = "设置智能体的模式"):
        super().__init__(name, description)

    def execute_command(self, text: str, guard: ModeGuard):
        if text == '/mode plan':
            guard.set_mode(AgentMode.PLAN)
        elif text == '/mode build':
            guard.set_mode(AgentMode.BUILD)
        else:
            print("未知的参数")
