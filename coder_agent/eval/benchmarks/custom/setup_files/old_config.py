# old_config.py — legacy config system; agent must migrate to new schema
#
# The old config used flat string keys with dot-notation (e.g. "db.host").
# The new schema uses nested dicts with typed validation (see config_v2_schema.py).
#
# The agent must:
# 1. Implement migrate_config(old: dict) -> dict that converts old-style flat keys
#    to the new nested structure expected by ConfigV2.
# 2. Implement load_config(path) that reads a YAML file and auto-detects schema version
#    (v1 = flat keys, v2 = nested).
# 3. Ensure all existing behaviour of OldConfig still works when wrapped.

import yaml
from pathlib import Path
from typing import Any


class OldConfig:
    """Legacy flat-key configuration store.

    Keys use dot-notation: "db.host", "server.port", "logging.level", etc.
    """

    DEFAULTS = {
        "db.host": "localhost",
        "db.port": "5432",
        "db.name": "myapp",
        "db.pool_size": "5",
        "server.host": "0.0.0.0",
        "server.port": "8080",
        "server.debug": "false",
        "logging.level": "INFO",
        "logging.format": "text",
        "cache.enabled": "true",
        "cache.ttl": "300",
    }

    def __init__(self, overrides: dict[str, str] | None = None):
        self._data = dict(self.DEFAULTS)
        if overrides:
            self._data.update(overrides)

    def get(self, key: str, default: Any = None) -> str:
        return self._data.get(key, default)

    def set(self, key: str, value: str) -> None:
        self._data[key] = value

    def as_dict(self) -> dict[str, str]:
        return dict(self._data)

    @classmethod
    def from_yaml(cls, path: str | Path) -> "OldConfig":
        raw = yaml.safe_load(Path(path).read_text())
        return cls(overrides=raw if isinstance(raw, dict) else None)


# -------------------------------------------------------------------
# TODO: implement the functions below
# -------------------------------------------------------------------

def migrate_config(old: dict[str, str]) -> dict:
    """Convert old flat-key dict to v2 nested dict structure.

    Example:
        {"db.host": "db.example.com", "server.port": "9000"} ->
        {"db": {"host": "db.example.com"}, "server": {"port": 9000}}

    Type coercion rules:
        - Keys ending in port, pool_size, ttl -> int
        - Keys ending in debug, enabled -> bool ("true"/"false")
        - Everything else -> str
    """
    raise NotImplementedError


def load_config(path: str | Path):
    """Load a config file, returning ConfigV2 regardless of schema version.

    If the YAML contains flat string keys -> call migrate_config() first.
    If the YAML contains nested dicts -> parse as ConfigV2 directly.
    """
    raise NotImplementedError
