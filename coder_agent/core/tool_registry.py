import os
from pathlib import Path

from coder_agent.tools.base import Tool


def build_tools(
    workspace: Path,
    *,
    enable_knowledge_retrieval: bool | None = None,
    fixed_knowledge_index_id: str | None = None,
) -> list[Tool]:
    from coder_agent.tools.file_tools import ListDirTool, PatchFileTool, ReadFileTool, WriteFileTool
    from coder_agent.tools.search_tool import SearchCodeTool
    from coder_agent.tools.shell_tool import RunCommandTool

    tools: list[Tool] = [
        ReadFileTool(workspace),
        WriteFileTool(workspace),
        PatchFileTool(workspace),
        ListDirTool(workspace),
        RunCommandTool(workspace),
        SearchCodeTool(workspace),
    ]

    # Keep accepted benchmark/default tool sets unchanged. Knowledge retrieval
    # is an explicit integration capability enabled by configuring its service.
    retrieval_configured = bool(os.environ.get("RAG_API_URL", "").strip())
    retrieval_enabled = (
        retrieval_configured
        if enable_knowledge_retrieval is None
        else enable_knowledge_retrieval and retrieval_configured
    )
    if retrieval_enabled:
        from coder_agent.tools.knowledge_retrieval import KnowledgeRetrievalTool

        tools.append(KnowledgeRetrievalTool(fixed_index_id=fixed_knowledge_index_id))

    return tools
