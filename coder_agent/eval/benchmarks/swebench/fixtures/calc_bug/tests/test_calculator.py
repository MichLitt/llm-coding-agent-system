from app.calculator import add


def test_add_positive_numbers() -> None:
    assert add(2, 3) == 5


def test_add_negative_numbers() -> None:
    assert add(-4, 1) == -3
