from app.service import get_system_status


def test_healthy_checks_report_ok() -> None:
    response = get_system_status([{"name": "db", "ok": True}])
    assert response["status"] == "ok"
    assert response["payload"]["healthy"] is True


def test_unhealthy_checks_report_degraded() -> None:
    response = get_system_status(
        [{"name": "db", "ok": True}, {"name": "cache", "ok": False}]
    )
    assert response["status"] == "degraded"
    assert response["payload"]["healthy"] is False
