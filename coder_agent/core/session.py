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

    def send(self, user_text: str, **run_options: Any) -> TurnResult:
        """Send one external user turn without resetting the underlying Agent.

        Optional arguments are forwarded to ``Agent.run`` for controlled
        evaluators (for example ``max_steps`` and a verification hook).  The
        existing one-argument interactive path is unchanged.
        """
        result = self.agent.run(user_text, **run_options)
        self.turns += 1
        return result

    def reset(self) -> None:
        self.agent.reset()
        self.turns = 0

    def close(self) -> None:
        if hasattr(self.agent, "close"):
            self.agent.close()

    def session_metadata(self) -> SessionMetadata:
        return SessionMetadata(
            model=getattr(self.agent, "_model_cfg").model,
            workspace=str(getattr(self.agent, "workspace", cfg.agent.workspace)),
            memory_enabled=getattr(self.agent, "memory", None) is not None,
            turns=self.turns,
        )
