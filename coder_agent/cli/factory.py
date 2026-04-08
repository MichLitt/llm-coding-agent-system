from dataclasses import replace
from pathlib import Path

from coder_agent.config import cfg, resolve_llm_profile
from coder_agent.core.agent import Agent, build_tools
from coder_agent.core.agent_types import ModelConfig
from coder_agent.core.llm_client import LLMClient
from coder_agent.core.session import AgentSession
from coder_agent.memory.manager import MemoryManager
from coder_agent.memory.trajectory import TrajectoryStore


CONFIG_PRESETS: dict[str, dict] = {
    "default": {},
    "C1": {"correction": False, "memory": False, "planning_mode": "direct"},
    "C2": {"correction": False, "memory": False, "planning_mode": "react"},
    "C3": {"correction": True, "memory": False, "planning_mode": "react"},
    "C4": {"correction": True, "memory": True, "planning_mode": "react"},
    "C5": {"correction": True, "memory": False, "planning_mode": "react", "checklist": True},
    "C6": {"correction": True, "memory": False, "planning_mode": "react", "verification_gate": True},
}
ACTIVE_PRESETS: tuple[str, ...] = ("default", "C3", "C4", "C6")
BENCHMARK_CANDIDATE_PRESETS: tuple[str, ...] = ("C3", "C4", "C6")
EXPERIMENTAL_PRESETS: tuple[str, ...] = ("C5",)


def resolve_agent_config(preset: str) -> dict:
    return dict(CONFIG_PRESETS.get(preset, {}))


def invalid_preset_labels(labels: list[str]) -> list[str]:
    return [label for label in labels if label not in CONFIG_PRESETS]


def make_agent(
    agent_config: dict | None = None,
    *,
    workspace: Path | None = None,
    experiment_config: dict | None = None,
    config_label: str | None = None,
    model: str | None = None,
    llm_profile: str | None = None,
    no_memory: bool = False,
    experiment_id: str = "default",
    trajectory_store: TrajectoryStore | None = None,
) -> Agent:
    # Resolve LLM profile — never mutates global cfg.model.*
    resolved_profile = resolve_llm_profile(llm_profile)
    if model:
        # --model overrides only the model name within this agent's profile
        resolved_profile = replace(resolved_profile, model=model)

    client = LLMClient(profile=resolved_profile)
    model_cfg = ModelConfig(
        model=resolved_profile.model,
        max_tokens=cfg.model.max_tokens,
        temperature=cfg.model.temperature,
        context_window_tokens=cfg.context.context_window_tokens,
    )

    resolved_agent_config = dict(agent_config or {})
    resolved_experiment_config = dict(experiment_config or {})
    resolved_workspace = Path(workspace or cfg.agent.workspace).resolve()
    memory_enabled = resolved_agent_config.get("memory", cfg.agent.enable_memory)
    memory = None
    if not no_memory and memory_enabled:
        db_path = cfg.agent.memory_db_path
        if config_label:
            db_path = db_path.parent / f"agent_memory_{config_label}.db"
        memory = MemoryManager(db_path)

    agent = Agent(
        tools=build_tools(resolved_workspace),
        client=client,
        model_config=model_cfg,
        memory=memory,
        trajectory_store=trajectory_store,
        experiment_id=experiment_id,
        experiment_config=resolved_agent_config,
        runtime_config=resolved_experiment_config,
        workspace=resolved_workspace,
    )
    return agent


def make_trajectory_store(trajectory_dir: str | None) -> TrajectoryStore | None:
    if not trajectory_dir:
        return None
    return TrajectoryStore(Path(trajectory_dir))


def make_session(
    *,
    model: str | None = None,
    llm_profile: str | None = None,
    no_memory: bool = False,
    agent_config: dict | None = None,
) -> AgentSession:
    return AgentSession(
        make_agent(
            agent_config=agent_config,
            model=model,
            llm_profile=llm_profile,
            no_memory=no_memory,
        )
    )
