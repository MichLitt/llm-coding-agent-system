# test_config_migration.py — do NOT modify this file
import pytest
import tempfile
import yaml
from pathlib import Path
from old_config import OldConfig, migrate_config, load_config
from config_v2_schema import ConfigV2


def test_old_config_get_default():
    cfg = OldConfig()
    assert cfg.get("db.host") == "localhost"
    assert cfg.get("server.port") == "8080"


def test_old_config_override():
    cfg = OldConfig({"db.host": "prod.example.com"})
    assert cfg.get("db.host") == "prod.example.com"


def test_migrate_config_basic():
    old = {"db.host": "db.example.com", "db.port": "5433"}
    result = migrate_config(old)
    assert result["db"]["host"] == "db.example.com"
    assert result["db"]["port"] == 5433  # coerced to int


def test_migrate_config_bool_coercion():
    old = {"server.debug": "true", "cache.enabled": "false"}
    result = migrate_config(old)
    assert result["server"]["debug"] is True
    assert result["cache"]["enabled"] is False


def test_migrate_config_full_defaults():
    old = OldConfig().as_dict()
    result = migrate_config(old)
    assert isinstance(result["db"]["pool_size"], int)
    assert isinstance(result["cache"]["ttl"], int)
    assert isinstance(result["server"]["port"], int)


def test_migrate_produces_valid_configv2():
    old = OldConfig().as_dict()
    nested = migrate_config(old)
    cfg = ConfigV2.from_dict(nested)
    assert cfg.db.host == "localhost"
    assert cfg.server.port == 8080
    assert cfg.cache.enabled is True


def test_load_config_v1_yaml(tmp_path):
    """load_config should handle v1 flat-key YAML files."""
    v1_yaml = tmp_path / "config_v1.yaml"
    v1_yaml.write_text(yaml.dump({"db.host": "v1host", "db.port": "5434"}))
    cfg = load_config(v1_yaml)
    assert isinstance(cfg, ConfigV2)
    assert cfg.db.host == "v1host"
    assert cfg.db.port == 5434


def test_load_config_v2_yaml(tmp_path):
    """load_config should handle v2 nested YAML files directly."""
    v2_yaml = tmp_path / "config_v2.yaml"
    v2_yaml.write_text(yaml.dump({"db": {"host": "v2host", "port": 5435}}))
    cfg = load_config(v2_yaml)
    assert isinstance(cfg, ConfigV2)
    assert cfg.db.host == "v2host"
    assert cfg.db.port == 5435


def test_load_config_missing_file():
    with pytest.raises(FileNotFoundError):
        load_config("/nonexistent/path/config.yaml")
