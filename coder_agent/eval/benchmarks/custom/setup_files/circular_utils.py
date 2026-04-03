# circular_utils.py — imports from circular_models, creating a circular dependency
#
# This module uses User only for a type hint in summarize_user().
# That import causes a circular dependency:
#   circular_models -> circular_utils -> circular_models
#
# Fix: use TYPE_CHECKING guard or a forward reference for the type hint,
# OR restructure so that format_name doesn't live here.

from circular_models import User   # causes circular import


def format_name(first: str, last: str) -> str:
    """Return 'Last, First' formatted name."""
    return f"{last}, {first}"


def summarize_user(user: "User") -> str:
    """Return a one-line summary of a User."""
    return f"User: {user.display_name()}"


def format_price(price: float, currency: str = "USD") -> str:
    """Format a price with currency symbol."""
    symbols = {"USD": "$", "EUR": "€", "GBP": "£"}
    symbol = symbols.get(currency, currency)
    return f"{symbol}{price:.2f}"
