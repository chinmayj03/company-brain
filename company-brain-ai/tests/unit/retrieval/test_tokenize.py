from companybrain.retrieval.tokenize import tokenize_code


def test_camel_case_split():
    assert tokenize_code("getUserId()") == ["get", "user", "id"]


def test_snake_case_split():
    assert tokenize_code("user_role_id") == ["user", "role", "id"]


def test_min_length_filter():
    assert "x" not in tokenize_code("x = 1")


def test_handles_empty():
    assert tokenize_code("") == []
    assert tokenize_code(None) == []


def test_digit_split():
    result = tokenize_code("user3D")
    assert "user" in result


def test_punctuation_stripped():
    result = tokenize_code("foo.bar(baz)")
    assert "foo" in result
    assert "bar" in result
    assert "baz" in result


def test_pascal_case_split():
    result = tokenize_code("PaymentService")
    assert "payment" in result
    assert "service" in result


def test_mixed_identifiers():
    result = tokenize_code("getPayerCompetitors competitiveness payer")
    assert "get" in result
    assert "payer" in result
    assert "competitors" in result
    assert "competitiveness" in result
