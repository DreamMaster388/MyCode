class Account:
    def __init__(self, owner: str, balance: float = 0.0) -> None:
        self.owner = owner
        self.balance = balance

    def deposit(self, amount: float) -> None:
        if amount <= 0:
            raise ValueError("amount must be positive")
        self.balance += amount

    def withdraw(self, amount: float) -> None:
        self.balance -= amount

    def apply_interest(self, rate: float, months: int) -> None:
        self.balance += self.balance * rate * months

    def statement(self) -> str:
        return f"{self.owner}: {self.balance:.2f}"
