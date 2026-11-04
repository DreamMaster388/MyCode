from .base import Command
from ..core.mode import ModeGuard
import re

class CommandRegistry:
    def __init__(self):
        self._commands: dict[str, Command] = {}

    def register(self, command: Command):
        self._commands[command.name] = command

    def check_command(self, text: str, guard: ModeGuard) -> bool:
        pattern = re.compile(r"^/(\w+)(?:\s.*)?$")
        match = pattern.fullmatch(text.strip())
        if match:
            # 只对命令名做大小写归一, 参数(尤其是路径)保持原样。
            cmd_name = match.group(1).lower()
            if cmd_name in self._commands:
                self._commands[cmd_name].execute_command(text, guard)
            else:
                print("未知命令")
            return True
        return False