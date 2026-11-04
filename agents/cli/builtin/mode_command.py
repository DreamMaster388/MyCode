from ..base import Command
from ..parsing import parse_command
from ...core.mode import ModeGuard, AgentMode

class ModeCommand(Command):
    def __init__(self, name: str = "mode", description: str = "设置智能体的模式"):
        super().__init__(name, description)

    def execute_command(self, text: str, guard: ModeGuard):
        name, args = parse_command(text)
        if name.lower() != self.name or len(args) != 1:
            print("未知的参数")
            return
        mode = args[0].lower()
        if mode == "plan":
            guard.set_mode(AgentMode.PLAN)
        elif mode == "build":
            guard.set_mode(AgentMode.BUILD)
        elif mode == "trusted":
            guard.set_mode(AgentMode.TRUSTED)
        else:
            print("未知的参数")
