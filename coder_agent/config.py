"""Runtime configuration: reads config.yaml, then allows .env overrides.

Priority (highest first):
  1. Environment variables (CODER_* / LLM_*)
  2. config.yaml values
  3. Hard-coded defaults

Usage
-----
    from coder_agent.config import cfg

    cfg.model.name          # backend model name
    cfg.agent.max_steps     # 15
    cfg.eval.trajectory_dir # Path("trajectories/")
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

load_dotenv(override=True)

_ROOT = Path(__file__).parent.parent


def _load_yaml() -> dict:
    yaml_path = _ROOT / "config.yaml"
    if yaml_path.exists():
        with open(yaml_path, encoding="utf-8") as file:
            return yaml.safe_load(file) or {}
    return {}


_Y = _load_yaml()


@dataclass
class ModelConfig:
    provider: str = _Y.get("model", {}).get("provider", "minimax")
    name: str = os.environ.get("CODER_MODEL", _Y.get("model", {}).get("name", "MiniMax-M2.7"))
    # api_format selects the SDK backend: "openai" (M2.5) or "anthropic" (M2.7)
    api_format: str = os.environ.get(
        "CODER_API_FORMAT", _Y.get("model", {}).get("api_format", "anthropic")
    )
    temperature: float = float(_Y.get("model", {}).get("temperature", 0.2))
    max_tokens: int = int(os.environ.get("CODER_MAX_TOKENS", _Y.get("model", {}).get("max_tokens", 8192)))
    seed: int = int(_Y.get("model", {}).get("seed", 42))

    # OpenAI-format credentials (M2.5 / other OpenAI-compatible endpoints)
    api_key: str = field(default_factory=lambda: os.environ.get("LLM_API_KEY", ""))
    base_url: str = field(default_factory=lambda: os.environ.get("LLM_BASE_URL", ""))

    # Anthropic-format credentials (M2.7 via MiniMax Token Plan)
    # Use MINIMAX_ANTHROPIC_BASE_URL to avoid collision with system ANTHROPIC_BASE_URL
    # (e.g. set by Claude desktop app to https://api.anthropic.com)
    anthropic_api_key: str = field(default_factory=lambda: os.environ.get("ANTHROPIC_API_KEY", ""))
    anthropic_base_url: str = field(default_factory=lambda: os.environ.get(
        "MINIMAX_ANTHROPIC_BASE_URL", "https://api.minimax.io/anthropic"
    ))


@dataclass
class AgentConfig:
    max_steps: int = int(os.environ.get("CODER_MAX_STEPS", _Y.get("agent", {}).get("max_steps", 15)))
    max_retries: int = int(os.environ.get("CODER_MAX_RETRIES", _Y.get("agent", {}).get("max_retries", 3)))
    planning_mode: str = _Y.get("agent", {}).get("planning_mode", "react")
    enable_correction: bool = os.environ.get(
        "CODER_CORRECTION_ENABLED",
        str(_Y.get("agent", {}).get("enable_correction", True)),
    ).lower() == "true"
    enable_memory: bool = os.environ.get(
        "CODER_MEMORY_ENABLED",
        str(_Y.get("agent", {}).get("enable_memory", False)),
    ).lower() == "true"
    enable_checklist: bool = os.environ.get(
        "CODER_CHECKLIST_ENABLED",
        str(_Y.get("agent", {}).get("enable_checklist", False)),
    ).lower() == "true"
    verbose: bool = os.environ.get("CODER_VERBOSE", "false").lower() == "true"
    # added by Stream A — defaults only
    doom_loop_threshold: int = int(_Y.get("agent", {}).get("doom_loop_threshold", 3))
    # added by Stream A — defaults only
    enable_approach_memory: bool = str(_Y.get("agent", {}).get("enable_approach_memory", False)).lower() == "true"
    # added by Stream A — defaults only
    memory_lookup_mode: str = _Y.get("agent", {}).get("memory_lookup_mode", "recency")
    workspace: Path = field(
        default_factory=lambda: Path(
            os.environ.get("CODER_WORKSPACE", str(_ROOT / "workspace"))
        ).resolve()
    )
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
    # added by Stream A — defaults only
    observation_compression_mode: str = _Y.get("context", {}).get("observation_compression_mode", "rule_based")
    # added by Stream A — defaults only
    history_compaction_mode: str = _Y.get("context", {}).get("history_compaction_mode", "rule_based")
    # added by Stream A — defaults only
    history_compaction_message_threshold: int = int(
        _Y.get("context", {}).get("history_compaction_message_threshold", 20)
    )
    # added by Stream A — defaults only
    keep_recent_turns: int = int(_Y.get("context", {}).get("keep_recent_turns", 6))
    context_window_tokens: int = 180_000


@dataclass
class EvalConfig:
    output_dir: Path = field(default_factory=lambda: _ROOT / _Y.get("eval", {}).get("output_dir", "results/"))
    trajectory_dir: Path = field(
        default_factory=lambda: _ROOT / _Y.get("eval", {}).get("trajectory_dir", "trajectories/")
    )
    random_seed: int = int(_Y.get("eval", {}).get("random_seed", 42))


@dataclass
class Config:
    model: ModelConfig = field(default_factory=ModelConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    tools: ToolsConfig = field(default_factory=ToolsConfig)
    context: ContextConfig = field(default_factory=ContextConfig)
    eval: EvalConfig = field(default_factory=EvalConfig)


def validate_config(config: "Config") -> None:
    """Fail fast with a clear message for obviously invalid config values."""
    if config.agent.max_steps < 1:
        raise ValueError(f"agent.max_steps must be >= 1, got {config.agent.max_steps}")
    if config.agent.max_retries < 0:
        raise ValueError(f"agent.max_retries must be >= 0, got {config.agent.max_retries}")
    if config.tools.terminal_timeout < 1:
        raise ValueError(f"tools.terminal_timeout must be >= 1, got {config.tools.terminal_timeout}")
    if config.model.api_format not in ("openai", "anthropic"):
        raise ValueError(
            f"model.api_format must be 'openai' or 'anthropic', got {config.model.api_format!r}"
        )


cfg = Config()
validate_config(cfg)

# Backward-compatible aliases.
MODEL = cfg.model.name
MAX_TOKENS = cfg.model.max_tokens
MAX_STEPS = cfg.agent.max_steps
MAX_RETRIES = cfg.agent.max_retries
WORKSPACE = cfg.agent.workspace
VERBOSE = cfg.agent.verbose
MEMORY_ENABLED = cfg.agent.enable_memory
MEMORY_DB_PATH = cfg.agent.memory_db_path
COMPRESSION_STRATEGY = cfg.context.compression_strategy
