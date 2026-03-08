from buggy_tree import BinarySearchTree


def test_insert_and_search():
    bst = BinarySearchTree()
    bst.insert(5)
    bst.insert(3)
    bst.insert(7)
    assert bst.search(5) is True
    assert bst.search(3) is True
    assert bst.search(7) is True
    assert bst.search(99) is False


def test_inorder_sorted():
    bst = BinarySearchTree()
    for v in [5, 3, 7, 1, 4, 6, 8]:
        bst.insert(v)
    assert bst.inorder() == [1, 3, 4, 5, 6, 7, 8]


def test_empty():
    bst = BinarySearchTree()
    assert bst.search(1) is False
    assert bst.inorder() == []


def test_duplicates():
    bst = BinarySearchTree()
    bst.insert(5)
    bst.insert(5)
    result = bst.inorder()
    assert result.count(5) == 2
