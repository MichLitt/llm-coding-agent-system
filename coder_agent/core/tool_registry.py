from coder_agent.tools.base import Tool


def build_tools() -> list[Tool]:
    from coder_agent.tools.file_tools import ListDirTool, ReadFileTool, WriteFileTool
    from coder_agent.tools.search_tool import SearchCodeTool
    from coder_agent.tools.shell_tool import RunCommandTool

    return [
        ReadFileTool(),
        WriteFileTool(),
        ListDirTool(),
        RunCommandTool(),
        SearchCodeTool(),
    ]
