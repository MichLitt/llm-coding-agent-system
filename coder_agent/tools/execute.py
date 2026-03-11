"""Tool execution utilities with parallel support."""

import asyncio
from typing import Any

from coder_agent.tools.base import Tool


def _is_tool_error_content(content: Any) -> bool:
    return isinstance(content, str) and content.startswith("Error:")


async def _execute_single(call: Any, tool_dict: dict[str, Tool]) -> dict[str, Any]:
    call_id = call["id"] if isinstance(call, dict) else call.id
    call_name = call["name"] if isinstance(call, dict) else call.name
    call_input = call["input"] if isinstance(call, dict) else call.input
    response = {"type": "tool_result", "tool_use_id": call_id}
    try:
        result = await tool_dict[call_name].execute(**call_input)
        response["content"] = str(result)
        if _is_tool_error_content(response["content"]):
            response["is_error"] = True
            response["error_kind"] = "tool_error"
    except KeyError:
        response["content"] = f"Error: tool '{call_name}' not found"
        response["is_error"] = True
        response["error_kind"] = "unknown_tool"
    except Exception as e:
        response["content"] = f"Error: {e}"
        response["is_error"] = True
        response["error_kind"] = "tool_error"
    return response


async def execute_tools(
    tool_calls: list[Any],
    tool_dict: dict[str, Tool],
    parallel: bool = True,
) -> list[dict[str, Any]]:
    if parallel:
        return list(await asyncio.gather(
            *[_execute_single(call, tool_dict) for call in tool_calls]
        ))
    return [await _execute_single(call, tool_dict) for call in tool_calls]
