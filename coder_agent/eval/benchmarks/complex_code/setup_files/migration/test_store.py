from store import migrate


def test_migration_preserves_already_migrated_record():
    assert migrate([{"id": "a", "version": 2}]) == [{"id": "a", "version": 2}]


def test_migration_is_idempotent():
    first = migrate([{"id": "a", "value": 1}])
    assert migrate(first) == first
