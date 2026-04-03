# test_observability.py — do NOT modify this file
import logging
import time
import pytest
from data_service import DataService, DataServiceError


def test_set_and_get_still_work():
    ds = DataService()
    ds.set("k1", "v1")
    assert ds.get("k1") == "v1"


def test_delete_still_works():
    ds = DataService()
    ds.set("x", 42)
    ds.delete("x")
    with pytest.raises(DataServiceError):
        ds.get("x")


def test_get_metrics_exists():
    ds = DataService()
    assert hasattr(ds, "get_metrics"), "DataService must have get_metrics()"
    metrics = ds.get_metrics()
    assert isinstance(metrics, dict)


def test_get_metrics_tracks_set_calls():
    ds = DataService()
    ds.set("a", 1)
    ds.set("b", 2)
    metrics = ds.get_metrics()
    assert metrics.get("set", {}).get("count", 0) == 2


def test_get_metrics_tracks_get_calls():
    ds = DataService()
    ds.set("a", 1)
    ds.get("a")
    ds.get("a")
    metrics = ds.get_metrics()
    assert metrics.get("get", {}).get("count", 0) == 2


def test_get_metrics_tracks_duration():
    ds = DataService()
    ds.set("k", "v")
    metrics = ds.get_metrics()
    assert "avg_duration_ms" in metrics.get("set", {}), \
        "Metrics must include avg_duration_ms per operation"
    assert metrics["set"]["avg_duration_ms"] >= 0


def test_logging_on_set(caplog):
    with caplog.at_level(logging.DEBUG):
        ds = DataService()
        ds.set("log_test", "hello")
    # At least one log record should mention "set" or the key
    messages = " ".join(r.message for r in caplog.records)
    assert "set" in messages.lower() or "log_test" in messages


def test_logging_on_error(caplog):
    with caplog.at_level(logging.WARNING):
        ds = DataService()
        with pytest.raises(DataServiceError):
            ds.get("nonexistent")
    messages = " ".join(r.message for r in caplog.records)
    assert len(caplog.records) > 0, "At least one log record expected on error"


def test_bulk_set_metrics():
    ds = DataService()
    ds.bulk_set({"a": 1, "b": 2, "c": 3})
    metrics = ds.get_metrics()
    # bulk_set either tracks its own count or delegates to set (either is valid)
    total_sets = metrics.get("set", {}).get("count", 0) + metrics.get("bulk_set", {}).get("count", 0)
    assert total_sets >= 1
