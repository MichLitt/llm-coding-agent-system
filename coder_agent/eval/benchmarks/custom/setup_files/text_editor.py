# text_editor.py — agent must extend with Command-pattern undo/redo stack
#
# Current: basic text editing operations (insert, delete, replace).
# Missing: undo() and redo() methods using the Command pattern.
#
# The agent must implement:
#   - Command base class (or protocol) with execute() and undo()
#   - Concrete commands: InsertCommand, DeleteCommand, ReplaceCommand
#   - Editor.undo() — reverse the last command
#   - Editor.redo() — re-apply the last undone command
#   - Editor.history — list of executed commands (for introspection)
#   - Undo stack should clear the redo stack on new edits

class Editor:
    """Simple line-based text editor."""

    def __init__(self, initial_content: str = ""):
        self._lines: list[str] = initial_content.splitlines() if initial_content else []

    def insert_line(self, index: int, text: str) -> None:
        """Insert a new line at the given index."""
        self._lines.insert(index, text)

    def delete_line(self, index: int) -> str:
        """Delete and return the line at the given index."""
        return self._lines.pop(index)

    def replace_line(self, index: int, text: str) -> str:
        """Replace the line at index with text. Returns old line."""
        old = self._lines[index]
        self._lines[index] = text
        return old

    def get_line(self, index: int) -> str:
        return self._lines[index]

    def line_count(self) -> int:
        return len(self._lines)

    def content(self) -> str:
        return "\n".join(self._lines)

    # TODO: implement undo() and redo() using the Command pattern
    # TODO: implement history property
