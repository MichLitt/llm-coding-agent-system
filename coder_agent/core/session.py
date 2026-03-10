from dataclasses import dataclass
from typing import Any

from coder_agent.config import cfg
from coder_agent.core.agent_types import TurnResult


@dataclass
class SessionMetadata:
    model: str
    workspace: str
    memory_enabled: bool
    turns: int


class AgentSession:
    def __init__(self, agent: Any):
        self.agent = agent
        self.turns = 0

    def send(self, user_text: str) -> TurnResult:
        result = self.agent.run(user_text)
        self.turns += 1
        return result

    def reset(self) -> None:
        self.agent.reset()
        self.turns = 0

    def session_metadata(self) -> SessionMetadata:
        return SessionMetadata(
            model=getattr(self.agent, "_model_cfg").model,
            workspace=str(cfg.agent.workspace),
            memory_enabled=getattr(self.agent, "memory", None) is not None,
            turns=self.turns,
        )
