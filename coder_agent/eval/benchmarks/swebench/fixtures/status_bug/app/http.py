def build_status_response(payload: dict, healthy: bool) -> dict:
    return {
        "status": "ok",
        "payload": payload,
    }
