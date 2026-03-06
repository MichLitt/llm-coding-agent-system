"""Tool execution utilities.

Provides execute_tools(), which takes a list of tool_use blocks from a
LLM response and dispatches them to the corresponding Tool instances.

Parallel execution (asyncio.gather) is used by default so multiple tool
calls in a single turn run concurrently — important when LLM calls, e.g.,
read_file on three files at once.

Functions
---------
execute_tools(tool_calls, tool_dict, parallel=True) -> list[dict]
    Dispatch and collect results; return a list of tool_result blocks
    ready to be appended as a user message.
"""

import asyncio
from typing import Any

from tools.base import Tool


async def _execute_single(
    call: Any, tool_dict: dict[str, Tool]
) -> dict[str, Any]:
    """Run one tool call and return a tool_result block.

    On error (unknown tool or exception in execute()), returns an
    is_error=True block with the error message so the agent can
    self-correct rather than crash.
    """
    call_id = call["id"] if isinstance(call, dict) else call.id
    call_name = call["name"] if isinstance(call, dict) else call.name
    call_input = call["input"] if isinstance(call, dict) else call.input
    response = {"type": "tool_result", "tool_use_id": call_id}
    try:
        result = await tool_dict[call_name].execute(**call_input)
        response["content"] = str(result)
    except KeyError:
        response["content"] = f"Error: tool '{call_name}' not found"
        response["is_error"] = True
    except Exception as e:
        response["content"] = f"Error: {e}"
        response["is_error"] = True
    return response


async def execute_tools(
    tool_calls: list[Any],
    tool_dict: dict[str, Tool],
    parallel: bool = True,
) -> list[dict[str, Any]]:
    """Execute all tool calls and return tool_result blocks.

    Parameters
    ----------
    tool_calls : list
        tool_use content blocks from a Claude response.
    tool_dict : dict
        Mapping of tool name -> Tool instance.
    parallel : bool
        If True (default), run all calls concurrently with asyncio.gather.
        Set False for sequential execution (easier to debug).
    """
    if parallel:
        return list(await asyncio.gather(
            *[_execute_single(call, tool_dict) for call in tool_calls]
        ))
    return [await _execute_single(call, tool_dict) for call in tool_calls]
