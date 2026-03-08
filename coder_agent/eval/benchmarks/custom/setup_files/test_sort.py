from buggy_sort import bubble_sort


def test_sort_empty():
    assert bubble_sort([]) == []


def test_sort_single():
    assert bubble_sort([1]) == [1]


def test_sort_sorted():
    assert bubble_sort([1, 2, 3]) == [1, 2, 3]


def test_sort_reverse():
    assert bubble_sort([3, 2, 1]) == [1, 2, 3]


def test_sort_random():
    assert bubble_sort([5, 3, 8, 1, 9, 2]) == [1, 2, 3, 5, 8, 9]


def test_sort_duplicates():
    assert bubble_sort([3, 1, 4, 1, 5, 9, 2, 6]) == [1, 1, 2, 3, 4, 5, 6, 9]
