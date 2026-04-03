# invoice.py — provided module; agent must write tests for it (do NOT modify)

from dataclasses import dataclass, field
from datetime import date
from typing import Optional


@dataclass
class LineItem:
    description: str
    quantity: float
    unit_price: float

    @property
    def subtotal(self) -> float:
        return self.quantity * self.unit_price


@dataclass
class Invoice:
    invoice_number: str
    customer: str
    issue_date: date
    due_date: date
    items: list[LineItem] = field(default_factory=list)
    discount_pct: float = 0.0   # 0–100
    tax_rate: float = 0.0        # 0–100
    currency: str = "USD"
    paid: bool = False

    def add_item(self, description: str, quantity: float, unit_price: float) -> None:
        if quantity <= 0:
            raise ValueError("quantity must be positive")
        if unit_price < 0:
            raise ValueError("unit_price cannot be negative")
        self.items.append(LineItem(description, quantity, unit_price))

    @property
    def subtotal(self) -> float:
        return sum(item.subtotal for item in self.items)

    @property
    def discount_amount(self) -> float:
        return self.subtotal * (self.discount_pct / 100)

    @property
    def taxable_amount(self) -> float:
        return self.subtotal - self.discount_amount

    @property
    def tax_amount(self) -> float:
        return self.taxable_amount * (self.tax_rate / 100)

    @property
    def total(self) -> float:
        return self.taxable_amount + self.tax_amount

    def apply_discount(self, pct: float) -> None:
        if not (0 <= pct <= 100):
            raise ValueError("discount_pct must be between 0 and 100")
        self.discount_pct = pct

    def mark_paid(self) -> None:
        self.paid = True

    def is_overdue(self, as_of: Optional[date] = None) -> bool:
        check_date = as_of or date.today()
        return not self.paid and check_date > self.due_date

    def summary(self) -> str:
        return (
            f"Invoice #{self.invoice_number} | {self.customer}\n"
            f"  Items: {len(self.items)}\n"
            f"  Subtotal: {self.subtotal:.2f} {self.currency}\n"
            f"  Discount ({self.discount_pct}%): -{self.discount_amount:.2f}\n"
            f"  Tax ({self.tax_rate}%): +{self.tax_amount:.2f}\n"
            f"  Total: {self.total:.2f} {self.currency}\n"
            f"  Paid: {self.paid}"
        )
