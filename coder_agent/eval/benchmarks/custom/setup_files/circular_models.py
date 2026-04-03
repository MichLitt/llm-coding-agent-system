# circular_models.py — has a circular import with circular_utils.py; agent must fix it
#
# The problem:
#   circular_models.py imports from circular_utils (for format_name)
#   circular_utils.py imports from circular_models (for User) -- see circular_utils.py
#
# Fix: break the cycle. One approach is to move format_name out of circular_utils,
# or use a lazy import inside the function that needs it.

from circular_utils import format_name   # causes circular import


class User:
    def __init__(self, first: str, last: str):
        self.first = first
        self.last = last

    def display_name(self) -> str:
        return format_name(self.first, self.last)


class Product:
    def __init__(self, name: str, price: float):
        self.name = name
        self.price = price

    def label(self) -> str:
        return f"{self.name} (${self.price:.2f})"
