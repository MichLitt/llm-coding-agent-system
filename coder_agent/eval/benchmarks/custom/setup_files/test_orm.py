# test_orm.py — do NOT modify this file
import pytest
from orm_base import Model


class User(Model):
    __tablename__ = "users"
    __columns__ = ["id INTEGER PRIMARY KEY", "name TEXT", "age INTEGER"]

    def __init__(self, id: int, name: str, age: int):
        self.id = id
        self.name = name
        self.age = age


@pytest.fixture(autouse=True)
def reset_db():
    User._reset()
    yield
    User._reset()


def _add_users():
    User(1, "Alice", 30).save()
    User(2, "Bob", 25).save()
    User(3, "Carol", 35).save()
    User(4, "Dave", 25).save()


def test_basic_crud():
    User(1, "Alice", 30).save()
    u = User.find_by_id(1)
    assert u.name == "Alice"


def test_query_all():
    _add_users()
    results = User.query().all()
    assert len(results) == 4


def test_query_filter():
    _add_users()
    results = User.query().filter(age=25).all()
    assert len(results) == 2
    assert all(u.age == 25 for u in results)


def test_query_filter_chaining():
    _add_users()
    results = User.query().filter(age=25).filter(name="Bob").all()
    assert len(results) == 1
    assert results[0].name == "Bob"


def test_query_order_by_asc():
    _add_users()
    results = User.query().order_by("age").all()
    ages = [u.age for u in results]
    assert ages == sorted(ages)


def test_query_order_by_desc():
    _add_users()
    results = User.query().order_by("age", desc=True).all()
    ages = [u.age for u in results]
    assert ages == sorted(ages, reverse=True)


def test_query_limit():
    _add_users()
    results = User.query().limit(2).all()
    assert len(results) == 2


def test_query_first():
    _add_users()
    result = User.query().filter(age=25).order_by("name").first()
    assert result is not None
    assert result.name == "Bob"


def test_query_first_returns_none_when_no_match():
    _add_users()
    result = User.query().filter(age=99).first()
    assert result is None


def test_query_count():
    _add_users()
    count = User.query().filter(age=25).count()
    assert count == 2


def test_query_count_all():
    _add_users()
    assert User.query().count() == 4
