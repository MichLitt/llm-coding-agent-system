def migrate(records):
    """Buggy migration: repeats duplicate already migrated records."""
    return [{**record, "version": record.get("version", 1) + 1} for record in records]
