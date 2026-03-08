"""Runtime configuration: reads config.yaml, then allows .env overrides.

Priority (highest first):
  1. Environment variables (CODER_* / LLM_*)
  2. config.yaml values
  3. Hard-coded defaults

Usage
-----
    from coder_agent.config import cfg

    cfg.model.name          # "MiniMax-M2.5"
    cfg.agent.max_steps     # 15
    cfg.eval.trajectory_dir # Path("trajectories/")
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

load_dotenv()

_ROOT = Path(__file__).parent.parent  # project root


def _load_yaml() -> dict:
    yaml_path = _ROOT / "config.yaml"
    if yaml_path.exists():
        with open(yaml_path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


_Y = _load_yaml()


@dataclass
class ModelConfig:
    provider: str = _Y.get("model", {}).get("provider", "minimax")
    name: str = os.environ.get("CODER_MODEL", _Y.get("model", {}).get("name", "MiniMax-M2.5"))
    temperature: float = float(_Y.get("model", {}).get("temperature", 0.2))
    max_tokens: int = int(os.environ.get("CODER_MAX_TOKENS", _Y.get("model", {}).get("max_tokens", 8192)))
    seed: int = int(_Y.get("model", {}).get("seed", 42))

    # LLM API credentials (from .env only — never put in YAML)
    api_key: str = field(default_factory=lambda: os.environ.get("LLM_API_KEY", ""))
    base_url: str = field(default_factory=lambda: os.environ.get("LLM_BASE_URL", ""))


@dataclass
class AgentConfig:
    max_steps: int = int(os.environ.get("CODER_MAX_STEPS", _Y.get("agent", {}).get("max_steps", 15)))
    max_retries: int = int(os.environ.get("CODER_MAX_RETRIES", _Y.get("agent", {}).get("max_retries", 3)))
    planning_mode: str = _Y.get("agent", {}).get("planning_mode", "react")
    enable_correction: bool = os.environ.get("CODER_CORRECTION_ENABLED", str(_Y.get("agent", {}).get("enable_correction", True))).lower() == "true"
    enable_memory: bool = os.environ.get("CODER_MEMORY_ENABLED", str(_Y.get("agent", {}).get("enable_memory", False))).lower() == "true"
    verbose: bool = os.environ.get("CODER_VERBOSE", "false").lower() == "true"
    workspace: Path = field(default_factory=lambda: Path(os.environ.get("CODER_WORKSPACE", str(_ROOT / "workspace"))).resolve())
    memory_db_path: Path = field(default_factory=lambda: _ROOT / "memory" / "agent_memory.db")


@dataclass
class ToolsConfig:
    terminal_timeout: int = int(_Y.get("tools", {}).get("terminal_timeout", 30))
    blocked_commands: list = field(default_factory=lambda: _Y.get("tools", {}).get(
        "blocked_commands",
        ["rm -rf /", "sudo", ":(){:|:&};:", "/dev/sda", "mkfs", "dd if=", "chmod -R 777 /"],
    ))


@dataclass
class ContextConfig:
    max_tokens: int = int(_Y.get("context", {}).get("max_tokens", 8000))
    summary_threshold: int = int(_Y.get("context", {}).get("summary_threshold", 6000))
    compression_strategy: str = os.environ.get(
        "CODER_COMPRESSION_STRATEGY",
        _Y.get("context", {}).get("compression_strategy", "rule_based"),
    )
    context_window_tokens: int = 180_000


@dataclass
class EvalConfig:
    output_dir: Path = field(default_factory=lambda: _ROOT / _Y.get("eval", {}).get("output_dir", "results/"))
    trajectory_dir: Path = field(default_factory=lambda: _ROOT / _Y.get("eval", {}).get("trajectory_dir", "trajectories/"))
    random_seed: int = int(_Y.get("eval", {}).get("random_seed", 42))


@dataclass
class Config:
    model: ModelConfig = field(default_factory=ModelConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    tools: ToolsConfig = field(default_factory=ToolsConfig)
    context: ContextConfig = field(default_factory=ContextConfig)
    eval: EvalConfig = field(default_factory=EvalConfig)


cfg = Config()

# ---------------------------------------------------------------------------
# Flat aliases for backward-compatibility with code that imports from config
# ---------------------------------------------------------------------------
MODEL = cfg.model.name
MAX_TOKENS = cfg.model.max_tokens
MAX_STEPS = cfg.agent.max_steps
MAX_RETRIES = cfg.agent.max_retries
WORKSPACE = cfg.agent.workspace
VERBOSE = cfg.agent.verbose
MEMORY_ENABLED = cfg.agent.enable_memory
MEMORY_DB_PATH = cfg.agent.memory_db_path
COMPRESSION_STRATEGY = cfg.context.compression_strategy
