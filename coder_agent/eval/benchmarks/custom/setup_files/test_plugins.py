# test_plugins.py — do NOT modify this file
import os
import sys
import tempfile
import textwrap
import pytest
from pathlib import Path
from app_skeleton import Application, PluginLoader, PluginError


PLUGIN_A = textwrap.dedent("""\
    PLUGIN_NAME = "greeter"
    PLUGIN_VERSION = "1.0.0"
    PLUGIN_DESCRIPTION = "Greets users"

    def run(context: dict) -> dict:
        name = context.get("name", "world")
        return {"message": f"Hello, {name}!"}
""")

PLUGIN_B = textwrap.dedent("""\
    PLUGIN_NAME = "doubler"
    PLUGIN_VERSION = "1.0.0"
    PLUGIN_DESCRIPTION = "Doubles a number"

    def run(context: dict) -> dict:
        value = context.get("value", 0)
        return {"result": value * 2}
""")

PLUGIN_UPDATED = textwrap.dedent("""\
    PLUGIN_NAME = "greeter"
    PLUGIN_VERSION = "2.0.0"

    def run(context: dict) -> dict:
        return {"message": "Updated greeting!"}
""")


@pytest.fixture
def plugin_dir(tmp_path):
    (tmp_path / "plugin_greeter.py").write_text(PLUGIN_A)
    (tmp_path / "plugin_doubler.py").write_text(PLUGIN_B)
    (tmp_path / "not_a_plugin.py").write_text("x = 1")  # should NOT be loaded
    return tmp_path


def test_load_all_discovers_plugins(plugin_dir):
    loader = PluginLoader()
    names = loader.load_all(plugin_dir)
    assert set(names) == {"greeter", "doubler"}


def test_load_all_ignores_non_plugin_files(plugin_dir):
    loader = PluginLoader()
    loader.load_all(plugin_dir)
    assert "not_a_plugin" not in loader.list_plugins()


def test_get_plugin(plugin_dir):
    loader = PluginLoader()
    loader.load_all(plugin_dir)
    plugin = loader.get("greeter")
    assert plugin.name == "greeter"


def test_get_unknown_plugin_raises(plugin_dir):
    loader = PluginLoader()
    loader.load_all(plugin_dir)
    with pytest.raises(PluginError):
        loader.get("nonexistent")


def test_plugin_run(plugin_dir):
    loader = PluginLoader()
    loader.load_all(plugin_dir)
    result = loader.get("greeter").run({"name": "Alice"})
    assert result == {"message": "Hello, Alice!"}


def test_application_run_plugin(plugin_dir):
    app = Application(plugin_dir)
    result = app.run_plugin("doubler", {"value": 21})
    assert result == {"result": 42}


def test_application_available_plugins(plugin_dir):
    app = Application(plugin_dir)
    assert set(app.available_plugins()) == {"greeter", "doubler"}


def test_hot_reload(plugin_dir):
    loader = PluginLoader()
    loader.load_all(plugin_dir)

    # Overwrite plugin file on disk
    (plugin_dir / "plugin_greeter.py").write_text(PLUGIN_UPDATED)
    loader.reload("greeter")

    result = loader.get("greeter").run({})
    assert result == {"message": "Updated greeting!"}


def test_plugin_metadata(plugin_dir):
    loader = PluginLoader()
    loader.load_all(plugin_dir)
    meta = loader.get("greeter").metadata
    assert meta["name"] == "greeter"
    assert meta["version"] == "1.0.0"
