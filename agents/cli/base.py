from abc import ABC, abstractmethod

class Command(ABC):
    def __init__(self, name: str, description: str):
        self.name = name
        self.description = description

    @abstractmethod
    def execute_command(self, text):
        pass