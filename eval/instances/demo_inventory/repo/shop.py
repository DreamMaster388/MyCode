class Product:
    def __init__(self, name: str, price: float, stock: int) -> None:
        self.name = name
        self.price = price
        self.stock = stock


class Inventory:
    def __init__(self) -> None:
        self._products = {}

    def add(self, product: Product) -> None:
        self._products[product.name] = product

    def get(self, name: str) -> Product:
        return self._products[name]

    def stock_of(self, name: str) -> int:
        return self._products[name].stock


class Order:
    def __init__(self, inventory: Inventory, items: dict) -> None:
        self.inventory = inventory
        self.items = items

    def place(self) -> float:
        total = 0.0
        for name, qty in self.items.items():
            product = self.inventory.get(name)
            if product.stock < qty:
                raise ValueError(f"insufficient stock for {name}")
            product.stock -= qty
            total += product.price * qty
        return total
