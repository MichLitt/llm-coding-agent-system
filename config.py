"""Runtime configuration loaded from environment variables / .env file.

Settings
--------
CODER_MODEL                 Model ID passed to the LLM backend (default: MiniMax-M2.5)
CODER_MAX_TOKENS            Max tokens per response (default: 8192)
CODER_MAX_STEPS             Max agent loop iterations (default: 20)
CODER_MAX_RETRIES           Max self-correction retries (default: 3)
CODER_WORKSPACE             Workspace directory (default: ./workspace)
CODER_VERBOSE               Print tool calls (default: false)
CODER_MEMORY_ENABLED        Enable SQLite long-term memory (default: false)
CODER_COMPRESSION_STRATEGY  Observation compression: rule_based | disabled (default: rule_based)
"""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

_ROOT = Path(__file__).parent

MODEL: str = os.environ.get("CODER_MODEL", "MiniMax-M2.5")
MAX_TOKENS: int = int(os.environ.get("CODER_MAX_TOKENS", "8192"))
MAX_STEPS: int = int(os.environ.get("CODER_MAX_STEPS", "20"))
MAX_RETRIES: int = int(os.environ.get("CODER_MAX_RETRIES", "3"))
WORKSPACE: Path = Path(os.environ.get("CODER_WORKSPACE", str(_ROOT / "workspace"))).resolve()
VERBOSE: bool = os.environ.get("CODER_VERBOSE", "false").lower() == "true"

# Memory & compression
MEMORY_ENABLED: bool = os.environ.get("CODER_MEMORY_ENABLED", "false").lower() == "true"
MEMORY_DB_PATH: Path = _ROOT / "memory" / "agent_memory.db"
COMPRESSION_STRATEGY: str = os.environ.get("CODER_COMPRESSION_STRATEGY", "rule_based")
