from messy_utils import process_list_a, process_list_b, process_list_c, format_result, format_metric


def test_process_list_a():
    assert process_list_a([1, 2, 3, -1, 0]) == 12


def test_process_list_a_all_negative():
    assert process_list_a([-1, -2, -3]) == 0


def test_process_list_b():
    assert process_list_b([1, 2, 3]) == 4.0


def test_process_list_b_empty():
    assert process_list_b([]) == 0


def test_process_list_c():
    assert process_list_c([1, 2, 3], 3) == 18


def test_format_result_large():
    assert "large" in format_result(2000, "total")


def test_format_result_medium():
    assert "medium" in format_result(500, "total")


def test_format_result_small():
    assert "small" in format_result(50, "total")


def test_format_metric():
    assert format_metric(2000, "value") == format_result(2000, "value")
