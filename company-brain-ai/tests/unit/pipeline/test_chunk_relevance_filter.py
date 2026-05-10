"""
ADR-0047: ChunkRelevanceFilter unit tests.

Verifies that tier-1 deterministic filters drop the right chunks without
any LLM call, and that language-agnostic behaviour holds across naming styles.
"""
from __future__ import annotations

import pytest

from companybrain.pipeline.chunk_relevance_filter import ChunkRelevanceFilter
from companybrain.pipeline.code_chunker import MethodChunk, _sha256


def _chunk(qname: str, body: str, kind: str = "method", language: str = "java") -> MethodChunk:
    return MethodChunk(
        file_path=f"Src.{language[:4]}",
        qname=qname,
        kind=kind,  # type: ignore[arg-type]
        body=body,
        header_context="",
        import_context="",
        body_hash=_sha256(body),
        language=language,
    )


class TestTier1BoilerplateName:

    def test_equals_dropped(self):
        c = _chunk("Foo.equals", "return Objects.equals(this, other);")
        results = ChunkRelevanceFilter().filter([c])
        assert not results[0].keep
        assert "boilerplate_name" in results[0].filter_reason

    def test_hashCode_dropped(self):
        c = _chunk("Foo.hashCode", "return Objects.hash(id, name);")
        results = ChunkRelevanceFilter().filter([c])
        assert not results[0].keep

    def test_toString_dropped(self):
        c = _chunk("Foo.toString", 'return "Foo{id=" + id + "}";')
        results = ChunkRelevanceFilter().filter([c])
        assert not results[0].keep

    def test_python_dunder_eq_dropped(self):
        c = _chunk("Foo.__eq__", "return self.id == other.id", language="python")
        results = ChunkRelevanceFilter().filter([c])
        assert not results[0].keep

    def test_python_dunder_repr_dropped(self):
        c = _chunk("Foo.__repr__", "return f'Foo(id={self.id})'", language="python")
        results = ChunkRelevanceFilter().filter([c])
        assert not results[0].keep


class TestTier1TrivialAccessor:

    def test_java_getter_dropped(self):
        body = "public String getName() {\n    return this.name;\n}"
        c = _chunk("Foo.getName", body)
        results = ChunkRelevanceFilter().filter([c])
        assert not results[0].keep
        assert "accessor" in results[0].filter_reason or "tier1" in results[0].filter_reason

    def test_java_setter_dropped(self):
        body = "public void setName(String name) {\n    this.name = name;\n}"
        c = _chunk("Foo.setName", body)
        results = ChunkRelevanceFilter().filter([c])
        assert not results[0].keep

    def test_python_getter_dropped(self):
        body = "def get_name(self):\n    return self.name"
        c = _chunk("Foo.get_name", body, language="python")
        results = ChunkRelevanceFilter().filter([c])
        assert not results[0].keep

    def test_getter_with_logic_kept(self):
        body = (
            "public String getName() {\n"
            "    if (this.name == null) {\n"
            "        this.name = loadFromDb();\n"
            "    }\n"
            "    return this.name;\n"
            "}"
        )
        c = _chunk("Foo.getName", body)
        results = ChunkRelevanceFilter().filter([c])
        assert results[0].keep


class TestTier1EmptyBody:

    def test_empty_braces_dropped(self):
        c = _chunk("Foo.init", "{}")
        results = ChunkRelevanceFilter().filter([c])
        assert not results[0].keep

    def test_pass_only_dropped(self):
        c = _chunk("Foo.noop", "pass", language="python")
        results = ChunkRelevanceFilter().filter([c])
        assert not results[0].keep

    def test_super_only_dropped(self):
        c = _chunk("Foo.init", "super();")
        results = ChunkRelevanceFilter().filter([c])
        assert not results[0].keep


class TestTier1KeepCases:

    def test_real_service_method_kept(self):
        body = (
            "public Order placeOrder(OrderRequest req) {\n"
            "    validate(req);\n"
            "    Order order = orderFactory.create(req);\n"
            "    paymentGateway.charge(order.getTotal());\n"
            "    return orderRepository.save(order);\n"
            "}"
        )
        c = _chunk("OrderService.placeOrder", body)
        results = ChunkRelevanceFilter().filter([c])
        assert results[0].keep

    def test_schema_block_always_kept(self):
        c = _chunk("users", "CREATE TABLE users (id UUID PRIMARY KEY);", kind="schema_block")
        results = ChunkRelevanceFilter().filter([c])
        assert results[0].keep

    def test_top_decl_always_kept(self):
        c = _chunk("Foo", "public class Foo extends Bar {}", kind="top_decl")
        results = ChunkRelevanceFilter().filter([c])
        assert results[0].keep


class TestTier2Reachability:

    def test_unreachable_dropped_when_set_provided(self):
        c1 = _chunk("Foo.reachable", "doSomething();")
        c2 = _chunk("Foo.unreachable", "doOther();")
        reachable = frozenset({"Foo.reachable"})
        results = ChunkRelevanceFilter().filter([c1, c2], reachable_qnames=reachable)
        assert results[0].keep
        assert not results[1].keep
        assert "tier2" in results[1].filter_reason

    def test_all_kept_when_no_reachability_set(self):
        chunks = [_chunk("Foo.a", "doA();"), _chunk("Bar.b", "doB();")]
        results = ChunkRelevanceFilter().filter(chunks, reachable_qnames=None)
        assert all(r.keep for r in results)


class TestTelemetry:

    def test_log_counts_match(self):
        chunks = [
            _chunk("Foo.equals", "return true;"),    # tier1 drop
            _chunk("Foo.real", "doWork();\nreturn x;" * 3),  # keep
        ]
        results = ChunkRelevanceFilter().filter(chunks)
        dropped = [r for r in results if not r.keep]
        kept    = [r for r in results if r.keep]
        assert len(dropped) == 1
        assert len(kept) == 1
        assert dropped[0].tier == 1
        assert kept[0].tier == 0
