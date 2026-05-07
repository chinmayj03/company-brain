import pytest
from companybrain.store.identity import make_entity_id, parse_entity_id, to_external_id


def test_make_entity_id():
    eid = make_entity_id("my-repo", "component", "PaymentService")
    assert eid == "my-repo::component::PaymentService"


def test_parse_entity_id():
    repo, etype, qname = parse_entity_id("my-repo::api_contract::POST_charge")
    assert repo == "my-repo"
    assert etype == "api_contract"
    assert qname == "POST_charge"


def test_parse_entity_id_invalid():
    with pytest.raises(ValueError):
        parse_entity_id("no-separators-here")


def test_to_external_id_passthrough():
    eid = "repo::component::Foo"
    assert to_external_id(eid) == eid


def test_round_trip():
    original = make_entity_id("svc", "data_model", "UserTable")
    parts = parse_entity_id(original)
    assert parts == ("svc", "data_model", "UserTable")
