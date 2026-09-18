from worker import resume
def test_completed_items_are_not_reprocessed(): assert resume(["a"], ["a", "b"]) == ["b"]
def test_order_of_new_items_is_preserved(): assert resume(["a"], ["b", "c"]) == ["b", "c"]
