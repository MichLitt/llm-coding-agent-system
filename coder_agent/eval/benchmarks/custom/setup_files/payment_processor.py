# payment_processor.py — agent must write a comprehensive test suite for this module

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class PaymentStatus(Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    REFUNDED = "refunded"


class PaymentMethod(Enum):
    CARD = "card"
    BANK_TRANSFER = "bank_transfer"
    WALLET = "wallet"


@dataclass
class Payment:
    id: str
    amount: float
    currency: str
    method: PaymentMethod
    status: PaymentStatus = PaymentStatus.PENDING
    created_at: datetime = field(default_factory=datetime.utcnow)
    metadata: dict = field(default_factory=dict)


class InsufficientFundsError(Exception):
    pass


class InvalidPaymentError(Exception):
    pass


class PaymentProcessor:
    """Processes payments with validation, idempotency, and refund support."""

    SUPPORTED_CURRENCIES = {"USD", "EUR", "GBP", "JPY"}
    MAX_AMOUNT = 100_000.0
    MIN_AMOUNT = 0.01

    def __init__(self):
        self._payments: dict[str, Payment] = {}
        self._wallets: dict[str, float] = {}  # wallet_id -> balance

    def create_wallet(self, wallet_id: str, initial_balance: float = 0.0) -> None:
        if initial_balance < 0:
            raise ValueError("Initial balance cannot be negative")
        self._wallets[wallet_id] = initial_balance

    def get_balance(self, wallet_id: str) -> float:
        if wallet_id not in self._wallets:
            raise KeyError(f"Wallet {wallet_id!r} not found")
        return self._wallets[wallet_id]

    def process(self, payment: Payment, wallet_id: Optional[str] = None) -> Payment:
        """Validate and process a payment. Returns the updated Payment."""
        self._validate(payment)

        if payment.id in self._payments:
            # Idempotency: return existing payment unchanged
            return self._payments[payment.id]

        if payment.method == PaymentMethod.WALLET:
            if wallet_id is None:
                raise InvalidPaymentError("wallet_id required for WALLET payments")
            balance = self.get_balance(wallet_id)
            if balance < payment.amount:
                payment.status = PaymentStatus.FAILED
                self._payments[payment.id] = payment
                raise InsufficientFundsError(
                    f"Balance {balance} < {payment.amount}"
                )
            self._wallets[wallet_id] -= payment.amount

        payment.status = PaymentStatus.COMPLETED
        self._payments[payment.id] = payment
        return payment

    def refund(self, payment_id: str, wallet_id: Optional[str] = None) -> Payment:
        """Refund a completed payment. Restores wallet balance if applicable."""
        if payment_id not in self._payments:
            raise KeyError(f"Payment {payment_id!r} not found")
        payment = self._payments[payment_id]
        if payment.status != PaymentStatus.COMPLETED:
            raise InvalidPaymentError(
                f"Cannot refund payment with status {payment.status.value}"
            )
        if payment.method == PaymentMethod.WALLET and wallet_id:
            self._wallets[wallet_id] = self._wallets.get(wallet_id, 0) + payment.amount
        payment.status = PaymentStatus.REFUNDED
        return payment

    def get_payment(self, payment_id: str) -> Payment:
        if payment_id not in self._payments:
            raise KeyError(f"Payment {payment_id!r} not found")
        return self._payments[payment_id]

    def list_by_status(self, status: PaymentStatus) -> list[Payment]:
        return [p for p in self._payments.values() if p.status == status]

    def _validate(self, payment: Payment) -> None:
        if payment.amount < self.MIN_AMOUNT:
            raise InvalidPaymentError(f"Amount must be >= {self.MIN_AMOUNT}")
        if payment.amount > self.MAX_AMOUNT:
            raise InvalidPaymentError(f"Amount must be <= {self.MAX_AMOUNT}")
        if payment.currency not in self.SUPPORTED_CURRENCIES:
            raise InvalidPaymentError(f"Unsupported currency: {payment.currency}")
        if not payment.id:
            raise InvalidPaymentError("Payment ID cannot be empty")
