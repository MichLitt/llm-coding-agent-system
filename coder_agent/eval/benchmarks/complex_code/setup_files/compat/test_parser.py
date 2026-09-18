from parser import parse

def test_legacy_comma_format(): assert parse("a,b") == ["a", "b"]
def test_new_format_omits_empty_fields(): assert parse("a;;b;") == ["a", "b"]
