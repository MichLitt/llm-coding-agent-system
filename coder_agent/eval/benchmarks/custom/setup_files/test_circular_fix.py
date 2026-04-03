# test_circular_fix.py — do NOT modify this file
import pytest


def test_import_models():
    from circular_models import User, Product
    assert User is not None
    assert Product is not None


def test_import_utils():
    from circular_utils import format_name, summarize_user, format_price
    assert format_name is not None


def test_user_display_name():
    from circular_models import User
    u = User("Jane", "Doe")
    assert u.display_name() == "Doe, Jane"


def test_format_name():
    from circular_utils import format_name
    assert format_name("Alice", "Smith") == "Smith, Alice"


def test_summarize_user():
    from circular_utils import summarize_user
    from circular_models import User
    u = User("Bob", "Brown")
    assert "Brown, Bob" in summarize_user(u)


def test_product_label():
    from circular_models import Product
    p = Product("Widget", 9.99)
    assert p.label() == "Widget ($9.99)"


def test_format_price():
    from circular_utils import format_price
    assert format_price(19.99) == "$19.99"
    assert format_price(5.0, "EUR") == "€5.00"


def test_no_import_error():
    """Importing both modules in order should not raise ImportError."""
    try:
        import circular_models
        import circular_utils
    except ImportError as e:
        pytest.fail(f"Circular import not fixed: {e}")
