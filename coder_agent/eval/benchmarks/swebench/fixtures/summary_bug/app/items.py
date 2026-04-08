def open_items(items: list[dict]) -> list[dict]:
    return [item for item in items if not item.get("done", False)]
