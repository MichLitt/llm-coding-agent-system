# app_skeleton.py — agent must implement plugin discovery and hot-reload
#
# The plugin system should:
#   1. Discover plugins by scanning a directory for Python files matching "plugin_*.py"
#   2. Each plugin file must expose: PLUGIN_NAME (str) and run(context: dict) -> dict
#   3. PluginLoader.load_all(directory) loads all discovered plugins
#   4. PluginLoader.reload(name) reloads a specific plugin from disk (hot-reload)
#   5. Application.run_plugin(name, context) executes a loaded plugin
#
# The agent must implement PluginLoader fully and ensure test_plugins.py passes.

import importlib.util
import sys
from pathlib import Path
from typing import Any, Optional


class PluginError(Exception):
    pass


class Plugin:
    """Represents a loaded plugin."""

    def __init__(self, name: str, module: Any):
        self.name = name
        self._module = module

    def run(self, context: dict) -> dict:
        if not hasattr(self._module, "run"):
            raise PluginError(f"Plugin {self.name!r} missing 'run' function")
        return self._module.run(context)

    @property
    def metadata(self) -> dict:
        return {
            "name": self.name,
            "version": getattr(self._module, "PLUGIN_VERSION", "0.0.0"),
            "description": getattr(self._module, "PLUGIN_DESCRIPTION", ""),
        }


class PluginLoader:
    """Discovers, loads, and hot-reloads plugins from a directory.

    Agent must implement:
    - load_all(directory): scan for plugin_*.py, load each as a Plugin
    - reload(name): reload the named plugin module from disk
    - get(name): return a loaded Plugin by name
    - list_plugins(): return names of all loaded plugins
    """

    def __init__(self):
        self._plugins: dict[str, Plugin] = {}
        self._paths: dict[str, Path] = {}

    def load_all(self, directory: str | Path) -> list[str]:
        """Load all plugin_*.py files from directory. Returns list of plugin names."""
        # TODO: implement
        raise NotImplementedError

    def reload(self, name: str) -> Plugin:
        """Reload a plugin by name from its original file path."""
        # TODO: implement
        raise NotImplementedError

    def get(self, name: str) -> Plugin:
        """Return a loaded Plugin by name. Raises PluginError if not found."""
        # TODO: implement
        raise NotImplementedError

    def list_plugins(self) -> list[str]:
        """Return names of all loaded plugins."""
        # TODO: implement
        raise NotImplementedError


class Application:
    """Simple application that runs plugins."""

    def __init__(self, plugin_dir: str | Path):
        self.plugin_dir = Path(plugin_dir)
        self.loader = PluginLoader()
        self.loader.load_all(self.plugin_dir)

    def run_plugin(self, name: str, context: Optional[dict] = None) -> dict:
        plugin = self.loader.get(name)
        return plugin.run(context or {})

    def available_plugins(self) -> list[str]:
        return self.loader.list_plugins()
