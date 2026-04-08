from app.items import open_items


def render_summary(items: list[dict]) -> str:
    names = ", ".join(item["title"] for item in items)
    return f"{len(items)} open items: {names}"
