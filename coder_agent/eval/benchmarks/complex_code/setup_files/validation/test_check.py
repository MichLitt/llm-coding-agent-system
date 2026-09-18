from check import valid
def test_filters_invalid(): assert valid([{"id":"a"}, {"bad":1}]) == [{"id":"a"}]
