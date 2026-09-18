from codec import encode
def test_metadata_nested(): assert encode("a", {"tag":"x"}) == {"name":"a", "metadata":{"tag":"x"}}
