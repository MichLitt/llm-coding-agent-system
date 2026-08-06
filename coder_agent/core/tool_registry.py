import os
from pathlib import Path

from coder_agent.tools.base import Tool


def build_tools(workspace: Path) -> list[Tool]:
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
    if os.environ.get("RAG_API_URL", "").strip():
        from coder_agent.tools.knowledge_retrieval import KnowledgeRetrievalTool

        tools.append(KnowledgeRetrievalTool())

    return tools
