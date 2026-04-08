from app.report import render_summary


def test_summary_only_counts_open_items() -> None:
    summary = render_summary(
        [
            {"title": "cache", "done": False},
            {"title": "docs", "done": True},
            {"title": "release", "done": False},
        ]
    )
    assert summary == "2 open items: cache, release"


def test_summary_handles_no_open_items() -> None:
    summary = render_summary(
        [
            {"title": "docs", "done": True},
        ]
    )
    assert summary == "0 open items: "
