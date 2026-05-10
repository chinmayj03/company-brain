"""Unit tests for ADR-0050 M2 XML partial parser."""
import pytest
from companybrain.util.xml_partial_parser import parse_complete_elements


def test_complete_xml_all_elements():
    raw = "<entity><name>Foo</name></entity><entity><name>Bar</name></entity>"
    result = parse_complete_elements(raw, "entity")
    assert len(result) == 2
    names = [e.findtext("name") for e in result]
    assert "Foo" in names
    assert "Bar" in names


def test_truncated_xml_salvages_complete():
    # Second entity is truncated mid-element
    raw = "<entity><name>Foo</name></entity><entity><name>Bar"
    result = parse_complete_elements(raw, "entity")
    assert len(result) == 1
    assert result[0].findtext("name") == "Foo"


def test_empty_string_returns_empty():
    assert parse_complete_elements("", "entity") == []


def test_totally_unparseable_returns_empty():
    assert parse_complete_elements("not xml at all <<<", "entity") == []


def test_wrong_tag_returns_empty():
    raw = "<entity><name>Foo</name></entity>"
    assert parse_complete_elements(raw, "method") == []


def test_nested_elements_preserved():
    raw = "<entity><name>Foo</name><meta><file>foo.java</file></meta></entity>"
    result = parse_complete_elements(raw, "entity")
    assert len(result) == 1
    assert result[0].findtext("meta/file") == "foo.java"


def test_multiple_truncated_keeps_last_complete():
    raw = (
        "<entity><name>A</name></entity>"
        "<entity><name>B</name></entity>"
        "<entity><name>C"   # truncated
    )
    result = parse_complete_elements(raw, "entity")
    assert len(result) == 2
    names = [e.findtext("name") for e in result]
    assert names == ["A", "B"]


def test_fragment_without_root_wrapper():
    # LLM may emit fragment with no outer root — the parser wraps it.
    raw = "<entity><name>Solo</name></entity>"
    result = parse_complete_elements(raw, "entity")
    assert len(result) == 1
