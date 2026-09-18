from events import accept
def test_duplicate_ids_only_emit_once(): assert accept([("a", 1), ("a", 1), ("b", 2)]) == [("a", 1), ("b", 2)]
def test_preserves_first_seen_order(): assert accept([("b", 2), ("a", 1)]) == [("b", 2), ("a", 1)]
