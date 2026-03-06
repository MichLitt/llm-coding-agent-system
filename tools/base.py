"""Base class for all agent tools.

Every concrete tool must:
1. Subclass Tool.
2. Pass name, description, and input_schema to super().__init__().
3. Implement async execute(**kwargs) -> str.

The input_schema follows JSON Schema (same format Claude function calling
uses). It is passed verbatim to the Claude API as the tool definition.

Example::

    class MyTool(Tool):
        def __init__(self):
            super().__init__(
                name="my_tool",
                description="Does something useful.",
                input_schema={
                    "type": "object",
                    "properties": {"arg": {"type": "string"}},
                    "required": ["arg"],
                },
            )

        async def execute(self, arg: str) -> str:
            return f"Result: {arg}"
"""

from dataclasses import dataclass
from typing import Any


@dataclass
class Tool:
    """Abstract base for all tools.

    Attributes:
        name: Unique tool identifier (must match what Claude will call).
        description: Human-readable description injected into the system
            prompt / tool schema so Claude knows when to use the tool.
        input_schema: JSON Schema object describing the tool's parameters.
    """

    name: str
    description: str
    input_schema: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """Serialise to the Claude API tool definition format."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }

    async def execute(self, **kwargs) -> str:
        """Execute the tool; return a plain-text result for agent.

        Subclasses MUST override this method.
        Exceptions should be caught internally and returned as error strings
        so the agent loop can continue rather than crash.
        """
        raise NotImplementedError("Tool subclasses must implement execute()")
