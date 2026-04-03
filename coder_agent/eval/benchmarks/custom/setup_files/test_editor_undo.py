# test_editor_undo.py — do NOT modify this file
import pytest
from text_editor import Editor


def test_insert_and_content():
    ed = Editor()
    ed.insert_line(0, "Hello")
    ed.insert_line(1, "World")
    assert ed.content() == "Hello\nWorld"


def test_undo_insert():
    ed = Editor()
    ed.insert_line(0, "Hello")
    ed.undo()
    assert ed.line_count() == 0


def test_undo_delete():
    ed = Editor("Line0\nLine1\nLine2")
    ed.delete_line(1)
    assert ed.line_count() == 2
    ed.undo()
    assert ed.line_count() == 3
    assert ed.get_line(1) == "Line1"


def test_undo_replace():
    ed = Editor("original")
    ed.replace_line(0, "modified")
    assert ed.get_line(0) == "modified"
    ed.undo()
    assert ed.get_line(0) == "original"


def test_redo_after_undo():
    ed = Editor()
    ed.insert_line(0, "A")
    ed.undo()
    ed.redo()
    assert ed.get_line(0) == "A"


def test_redo_cleared_on_new_edit():
    ed = Editor()
    ed.insert_line(0, "A")
    ed.undo()
    ed.insert_line(0, "B")    # new edit should clear redo stack
    with pytest.raises(Exception):
        ed.redo()              # nothing to redo


def test_multiple_undo_redo():
    ed = Editor()
    ed.insert_line(0, "first")
    ed.insert_line(1, "second")
    ed.insert_line(2, "third")
    ed.undo()
    ed.undo()
    assert ed.line_count() == 1
    ed.redo()
    assert ed.line_count() == 2
    assert ed.get_line(1) == "second"


def test_undo_empty_raises():
    ed = Editor()
    with pytest.raises(Exception):
        ed.undo()


def test_redo_empty_raises():
    ed = Editor()
    with pytest.raises(Exception):
        ed.redo()


def test_history_attribute():
    ed = Editor()
    assert hasattr(ed, "history"), "Editor must expose a 'history' attribute"
    ed.insert_line(0, "x")
    assert len(ed.history) == 1


def test_history_length_grows():
    ed = Editor()
    ed.insert_line(0, "a")
    ed.insert_line(1, "b")
    assert len(ed.history) == 2
