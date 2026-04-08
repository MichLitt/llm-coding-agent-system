from app.http import build_status_response


def get_system_status(checks: list[dict]) -> dict:
    healthy = all(check.get("ok", False) for check in checks)
    payload = {
        "checks": checks,
        "healthy": healthy,
    }
    return build_status_response(payload, healthy=healthy)
