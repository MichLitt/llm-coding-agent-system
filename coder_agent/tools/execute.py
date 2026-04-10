"""Tool execution utilities with parallel support."""

import asyncio
import time
from typing import Any

from coder_agent.tools.base import Tool
from coder_agent.tools.command_budget import is_ad_hoc_install_command


def _is_tool_error_content(content: Any) -> bool:
    return isinstance(content, str) and content.startswith("Error:")


async def _execute_single(call: Any, tool_dict: dict[str, Tool]) -> dict[str, Any]:
    started_at = time.perf_counter()
    try:
        call_id = call["id"] if isinstance(call, dict) else call.id
        call_name = call["name"] if isinstance(call, dict) else call.name
        call_input = call["input"] if isinstance(call, dict) else call.input
    except (KeyError, AttributeError) as e:
        return {
            "type": "tool_result",
            "tool_use_id": "",
            "content": f"Error: malformed tool call ({e})",
            "is_error": True,
            "error_kind": "tool_error",
            "duration_ms": int((time.perf_counter() - started_at) * 1000),
        }
    response = {"type": "tool_result", "tool_use_id": call_id}
    if call_name == "__budget_error__":
        response["content"] = str(call_input.get("message", "Error: tool execution failed"))
        response["is_error"] = True
        response["error_kind"] = "tool_error"
        response["duration_ms"] = int((time.perf_counter() - started_at) * 1000)
        return response
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
    response["duration_ms"] = int((time.perf_counter() - started_at) * 1000)
    return response


async def execute_tools(
    tool_calls: list[Any],
    tool_dict: dict[str, Tool],
    parallel: bool = True,
) -> list[dict[str, Any]]:
    prepared_calls = _reserve_install_budget(tool_calls, tool_dict)
    if parallel:
        return list(await asyncio.gather(
            *[_execute_single(call, tool_dict) for call in prepared_calls]
        ))
    return [await _execute_single(call, tool_dict) for call in prepared_calls]


def _reserve_install_budget(tool_calls: list[Any], tool_dict: dict[str, Tool]) -> list[Any]:
    run_command_tool = tool_dict.get("run_command")
    if run_command_tool is None or not hasattr(run_command_tool, "try_reserve_ad_hoc_install"):
        return tool_calls

    prepared_calls: list[Any] = []
    for call in tool_calls:
        call_name = call["name"] if isinstance(call, dict) else getattr(call, "name", "")
        if call_name != "run_command":
            prepared_calls.append(call)
            continue

        call_input = dict(call["input"] if isinstance(call, dict) else getattr(call, "input", {}) or {})
        command = str(call_input.get("command", ""))
        budget_error = run_command_tool.try_reserve_ad_hoc_install(command)
        if budget_error:
            prepared_calls.append(
                {
                    "id": call["id"] if isinstance(call, dict) else getattr(call, "id", ""),
                    "name": "__budget_error__",
                    "input": {"message": budget_error},
                }
            )
            continue

        if is_ad_hoc_install_command(command):
            call_input["_install_budget_reserved"] = True
        if isinstance(call, dict):
            prepared_calls.append({**call, "input": call_input})
        else:
            prepared_calls.append(call)
    return prepared_calls
