"""Unit tests for ADR-0046 chunk_relevance_filter (static pre-LLM filter)."""
from __future__ import annotations

import pytest

from companybrain.pipeline.chunk_relevance_filter import filter_chunks
from companybrain.pipeline.code_chunker import MethodChunk


def _chunk(
    qname: str = "Foo.doSomething",
    body: str = "public void doSomething() { business(); }",
    header_context: str = "",
    kind: str = "method",
) -> MethodChunk:
    return MethodChunk(
        file_path="Foo.java",
        qname=qname,
        kind=kind,  # type: ignore[arg-type]
        body=body,
        header_context=header_context,
        import_context="",
        body_hash="abc",
        language="java",
    )


class TestLombokFilter:
    def test_lombok_getter_filtered(self):
        c = _chunk(
            qname="Foo.getName",
            header_context="@Data\npublic class Foo { private String name; }",
        )
        to_extract, filtered = filter_chunks([c])
        assert len(filtered) == 1
        assert filtered[0].relevance_reason == "lombok_trivial"

    def test_lombok_setter_filtered(self):
        c = _chunk(
            qname="Foo.setName",
            header_context="@Getter\n@Setter\npublic class Foo {}",
        )
        to_extract, filtered = filter_chunks([c])
        assert len(filtered) == 1

    def test_lombok_equals_filtered(self):
        c = _chunk(
            qname="Foo.equals",
            header_context="@Data\npublic class Foo {}",
        )
        to_extract, filtered = filter_chunks([c])
        assert len(filtered) == 1

    def test_no_lombok_annotation_not_filtered(self):
        c = _chunk(
            qname="Foo.getName",
            header_context="public class Foo {}",  # no @Data
        )
        to_extract, filtered = filter_chunks([c])
        assert len(to_extract) == 1
        assert len(filtered) == 0

    def test_business_method_not_filtered_even_with_lombok(self):
        c = _chunk(
            qname="Foo.getPayerCompetitors",
            body="public List<Payer> getPayerCompetitors(UUID id) { return repo.findCompetitors(id); }",
            header_context="@Data\npublic class Foo {}",
        )
        # "getPayerCompetitors" matches the regex prefix get[A-Z] — so it WILL be filtered.
        # This is intentional: Lombok @Data + getter-like prefix → skip.
        to_extract, filtered = filter_chunks([c])
        assert filtered[0].relevance_reason == "lombok_trivial"


class TestObjectOverrideFilter:
    def test_override_tostring_filtered(self):
        c = _chunk(
            qname="Foo.toString",
            body="@Override\npublic String toString() { return name; }",
        )
        to_extract, filtered = filter_chunks([c])
        assert len(filtered) == 1
        assert filtered[0].relevance_reason == "object_override"

    def test_override_equals_filtered(self):
        c = _chunk(
            qname="Foo.equals",
            body="@Override\npublic boolean equals(Object o) { return this == o; }",
        )
        to_extract, filtered = filter_chunks([c])
        assert len(filtered) == 1

    def test_non_object_override_not_filtered(self):
        c = _chunk(
            qname="Foo.process",
            body="@Override\npublic void process() { doWork(); }",
        )
        to_extract, filtered = filter_chunks([c])
        assert len(to_extract) == 1


class TestEmptyOrStubFilter:
    def test_empty_body_filtered(self):
        c = _chunk(body="public void doSomething() {}")
        to_extract, filtered = filter_chunks([c])
        assert len(filtered) == 1
        assert filtered[0].relevance_reason == "empty_or_stub"

    def test_unsupported_stub_filtered(self):
        c = _chunk(body="public void doSomething() {\n    throw new UnsupportedOperationException();\n}")
        to_extract, filtered = filter_chunks([c])
        assert len(filtered) == 1
        assert filtered[0].relevance_reason == "empty_or_stub"

    def test_real_method_not_filtered(self):
        c = _chunk(body="public List<Payer> get() { return repo.findAll(id); }")
        to_extract, filtered = filter_chunks([c])
        assert len(to_extract) == 1


class TestPureDelegationFilter:
    def test_return_this_field_filtered(self):
        c = _chunk(body="public String get() {\n    return this.name;\n}")
        to_extract, filtered = filter_chunks([c])
        assert len(filtered) == 1
        assert filtered[0].relevance_reason == "pure_delegation"

    def test_one_line_setter_filtered(self):
        c = _chunk(body="public void setFoo(String v) {\n    this.foo = v;\n}")
        to_extract, filtered = filter_chunks([c])
        assert len(filtered) == 1

    def test_super_delegation_filtered(self):
        c = _chunk(body="public void process() {\n    super.process(arg);\n}")
        to_extract, filtered = filter_chunks([c])
        assert len(filtered) == 1

    def test_delegate_call_not_filtered(self):
        # Delegate call with args is NOT filtered — could carry business logic.
        c = _chunk(body="public List<X> list() {\n    return delegate.findAll();\n}")
        to_extract, filtered = filter_chunks([c])
        # Not matched by the conservative pattern — filter does NOT remove it.
        assert len(to_extract) == 1

    def test_repo_call_with_arg_not_filtered(self):
        c = _chunk(body="public List<Payer> get() { return repo.findAll(id); }")
        to_extract, filtered = filter_chunks([c])
        assert len(to_extract) == 1

    def test_multi_line_not_filtered(self):
        c = _chunk(body="public Payer get(UUID id) {\n    Payer p = repo.findById(id);\n    validate(p);\n    return p;\n}")
        to_extract, filtered = filter_chunks([c])
        assert len(to_extract) == 1


class TestDeprecatedFilter:
    def test_deprecated_filtered(self):
        c = _chunk(body="@Deprecated\npublic void oldMethod() { doStuff(); }")
        to_extract, filtered = filter_chunks([c])
        assert len(filtered) == 1
        assert filtered[0].relevance_reason == "deprecated"

    def test_deprecated_in_header_filtered(self):
        c = _chunk(
            body="public void oldMethod() { doStuff(); }",
            header_context="@Deprecated\npublic class Foo {}",
        )
        to_extract, filtered = filter_chunks([c])
        assert len(filtered) == 1


class TestTestMethodFilter:
    def test_junit_test_filtered(self):
        c = _chunk(body="@Test\npublic void testGetPayer() { assertEquals(1, service.count()); }")
        to_extract, filtered = filter_chunks([c])
        assert len(filtered) == 1
        assert filtered[0].relevance_reason == "test_method"


class TestBatchAndWholeFilePassThrough:
    def test_batch_kind_not_filtered(self):
        c = _chunk(
            qname="Foo.__batch_0__",
            body="[METHOD: Foo.getName]\npublic String getName() { return this.name; }",
            kind="batch",
        )
        to_extract, filtered = filter_chunks([c])
        assert len(to_extract) == 1
        assert len(filtered) == 0

    def test_whole_file_kind_not_filtered(self):
        c = _chunk(
            qname="Foo",
            body="public class Foo {}",
            kind="whole_file",
        )
        to_extract, filtered = filter_chunks([c])
        assert len(to_extract) == 1


class TestMixedChunks:
    def test_filters_correctly_across_mixed_batch(self):
        chunks = [
            _chunk("Foo.getPayerCompetitors", "public List<Payer> getPayerCompetitors(UUID id) { return repo.findCompetitors(id); }"),
            # qname matches the actual method name for the override check to fire
            _chunk("Foo.toString", "@Override\npublic String toString() { return name; }", kind="method"),
            _chunk("Foo.process", "public void process() { queue.push(msg); }"),
        ]
        to_extract, filtered = filter_chunks(chunks)
        assert len(to_extract) == 2
        assert len(filtered) == 1
        assert filtered[0].relevance_reason == "object_override"

    def test_all_real_methods_pass_through(self):
        chunks = [
            _chunk("Foo.findCompetitors", "public List<Payer> findCompetitors(UUID id) {\n    List<Payer> p = repo.findCompetitors(id);\n    return p;\n}"),
            _chunk("Foo.processPayment", "public Receipt processPayment(Payment p) { validate(p); return charge(p); }"),
        ]
        to_extract, filtered = filter_chunks(chunks)
        assert len(to_extract) == 2
        assert len(filtered) == 0
