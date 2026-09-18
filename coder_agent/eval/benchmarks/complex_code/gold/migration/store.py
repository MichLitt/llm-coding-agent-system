def migrate(records):
    """Upgrade v1 records once while preserving a completed migration."""
    return [{**record, "version": record.get("version", 1) if record.get("version") == 2 else 2} for record in records]
