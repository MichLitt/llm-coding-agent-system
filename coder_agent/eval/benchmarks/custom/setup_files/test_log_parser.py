# test_log_parser.py — do NOT modify this file
import pytest
from pathlib import Path
from log_parser import LogEntry, parse_log_file, get_errors, get_by_service


SAMPLE = Path(__file__).parent / "sample_logs.jsonl"


def test_parse_returns_log_entries():
    entries = parse_log_file(SAMPLE)
    assert len(entries) > 0
    assert all(isinstance(e, LogEntry) for e in entries)


def test_malformed_lines_skipped():
    entries = parse_log_file(SAMPLE)
    # sample_logs.jsonl has 3 malformed lines; valid entries should be < total lines
    total_lines = sum(1 for _ in SAMPLE.open())
    assert len(entries) < total_lines


def test_log_entry_fields():
    entries = parse_log_file(SAMPLE)
    first = entries[0]
    assert hasattr(first, "timestamp")
    assert hasattr(first, "level")
    assert hasattr(first, "service")
    assert hasattr(first, "message")


def test_get_errors_returns_only_errors():
    entries = parse_log_file(SAMPLE)
    errors = get_errors(entries)
    assert len(errors) > 0
    assert all(e.level in ("ERROR", "CRITICAL") for e in errors)


def test_get_by_service_filters_correctly():
    entries = parse_log_file(SAMPLE)
    db_entries = get_by_service(entries, "db")
    assert len(db_entries) > 0
    assert all(e.service == "db" for e in db_entries)


def test_malformed_count_exactly_three():
    """sample_logs.jsonl contains exactly 3 malformed lines."""
    entries = parse_log_file(SAMPLE)
    total_lines = sum(1 for _ in SAMPLE.open() if _.strip())
    assert len(entries) == total_lines - 3


def test_parse_string_content():
    """parse_log_file also accepts a string path."""
    entries = parse_log_file(str(SAMPLE))
    assert len(entries) > 0
