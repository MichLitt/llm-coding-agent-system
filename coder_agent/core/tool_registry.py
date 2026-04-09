from pathlib import Path

from coder_agent.tools.base import Tool


def build_tools(workspace: Path) -> list[Tool]:
    from coder_agent.tools.file_tools import ListDirTool, PatchFileTool, ReadFileTool, WriteFileTool
    from coder_agent.tools.search_tool import SearchCodeTool
    from coder_agent.tools.shell_tool import RunCommandTool

    return [
        ReadFileTool(workspace),
        WriteFileTool(workspace),
        PatchFileTool(workspace),
        ListDirTool(workspace),
        RunCommandTool(workspace),
        SearchCodeTool(workspace),
    ]
