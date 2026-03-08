from utils import add, subtract, multiply, divide, reverse_string, count_vowels, is_palindrome


def test_add():
    assert add(2, 3) == 5

def test_subtract():
    assert subtract(5, 3) == 2

def test_multiply():
    assert multiply(4, 3) == 12

def test_divide():
    assert divide(10, 2) == 5.0

def test_divide_by_zero():
    import pytest
    with pytest.raises(ValueError):
        divide(1, 0)

def test_reverse_string():
    assert reverse_string("hello") == "olleh"

def test_count_vowels():
    assert count_vowels("Hello World") == 3

def test_is_palindrome():
    assert is_palindrome("racecar") is True
    assert is_palindrome("hello") is False
    assert is_palindrome("A man a plan a canal Panama") is True
