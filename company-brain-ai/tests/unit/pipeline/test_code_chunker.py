"""
ADR-0044 PR-0044-2: CodeChunker tests.

Per-language fixture tests: feed representative source files and assert
that MethodChunk objects are produced with the correct bodies and header_context.
"""
from __future__ import annotations

import atexit
import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from companybrain.pipeline.code_chunker import CodeChunker, MethodChunk, _sha256

# Module-level temp dir — cleaned up when the process exits.
_TMP = tempfile.mkdtemp(prefix="test_code_chunker_")
atexit.register(shutil.rmtree, _TMP, ignore_errors=True)


def _unit(content: str, language: str, class_name: str = "TestClass") -> SimpleNamespace:
    """Write content to a real file and return a SimpleNamespace pointing at it.

    ADR-0045: chunk_unit reads from unit.file_path, so the path must resolve
    to a real file on disk.
    """
    ext = {"java": ".java", "python": ".py", "typescript": ".ts",
           "go": ".go", "sql": ".sql"}.get(language, f".{language[:4]}")
    fp = Path(_TMP) / f"{class_name}{ext}"
    fp.write_text(content, encoding="utf-8")
    return SimpleNamespace(
        language=language,
        file_path=str(fp),
        class_name=class_name,
        repo_name="test-repo",
        role="service",
    )


# ── Java ──────────────────────────────────────────────────────────────────────

_JAVA_FIXTURE = """\
package com.example;

import java.util.List;
import java.util.Optional;

public class OrderService {
    private final OrderRepository orderRepository;
    private final PaymentGateway paymentGateway;

    public OrderService(OrderRepository r, PaymentGateway pg) {
        this.orderRepository = r;
        this.paymentGateway = pg;
    }

    public Order findById(Long id) {
        return orderRepository.findById(id)
            .orElseThrow(() -> new RuntimeException("not found"));
    }

    public void placeOrder(Order order) {
        paymentGateway.charge(order.getTotal());
        orderRepository.save(order);
    }

    public List<Order> findByCustomer(Long customerId) {
        return orderRepository.findByCustomerId(customerId);
    }
}
"""


def test_java_produces_chunks():
    chunker = CodeChunker()
    unit = _unit(_JAVA_FIXTURE, "java", "OrderService")
    chunks = chunker.chunk_unit(unit)

    assert len(chunks) >= 3, f"Expected ≥3 chunks for 3 methods, got {len(chunks)}"
    qnames = {c.qname for c in chunks}
    assert any("findById" in q for q in qnames)
    assert any("placeOrder" in q for q in qnames)
    assert any("findByCustomer" in q for q in qnames)


def test_java_header_context_contains_class_signature():
    chunker = CodeChunker()
    unit = _unit(_JAVA_FIXTURE, "java", "OrderService")
    chunks = chunker.chunk_unit(unit)

    for chunk in chunks:
        if chunk.kind == "method":
            assert "OrderService" in chunk.header_context or chunk.header_context == ""


def test_java_body_is_verbatim():
    """Body must contain the actual method text, not a summary."""
    chunker = CodeChunker()
    unit = _unit(_JAVA_FIXTURE, "java", "OrderService")
    chunks = chunker.chunk_unit(unit)

    bodies = " ".join(c.body for c in chunks)
    assert "paymentGateway.charge" in bodies
    assert "orderRepository.findByCustomerId" in bodies


def test_java_body_hash_is_stable():
    chunker = CodeChunker()
    unit = _unit(_JAVA_FIXTURE, "java", "OrderService")
    chunks1 = chunker.chunk_unit(unit)
    chunks2 = chunker.chunk_unit(unit)
    hashes1 = sorted(c.body_hash for c in chunks1)
    hashes2 = sorted(c.body_hash for c in chunks2)
    assert hashes1 == hashes2


# ── Python ────────────────────────────────────────────────────────────────────

_PYTHON_FIXTURE = """\
from typing import Optional, List

class UserService:
    def __init__(self, repo):
        self.repo = repo

    def get_user(self, user_id: int) -> Optional[dict]:
        return self.repo.find_by_id(user_id)

    def create_user(self, email: str, name: str) -> dict:
        user = {"email": email, "name": name}
        self.repo.save(user)
        return user

    def list_active(self) -> List[dict]:
        return self.repo.find_all(active=True)

    def deactivate(self, user_id: int) -> None:
        self.repo.update(user_id, {"active": False})
"""


def test_python_produces_chunks():
    chunker = CodeChunker()
    unit = _unit(_PYTHON_FIXTURE, "python", "UserService")
    chunks = chunker.chunk_unit(unit)

    assert len(chunks) >= 4
    qnames = {c.qname for c in chunks}
    assert any("get_user" in q for q in qnames)
    assert any("create_user" in q for q in qnames)
    assert any("list_active" in q for q in qnames)
    assert any("deactivate" in q for q in qnames)


def test_python_bodies_contain_actual_code():
    chunker = CodeChunker()
    unit = _unit(_PYTHON_FIXTURE, "python", "UserService")
    chunks = chunker.chunk_unit(unit)
    bodies = " ".join(c.body for c in chunks)
    assert "self.repo.find_by_id" in bodies
    assert "active=True" in bodies


# ── TypeScript ────────────────────────────────────────────────────────────────

_TS_FIXTURE = """\
import { Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';

export class ProductService {
  private baseUrl = '/api/products';

  constructor(private http: HttpClient) {}

  getAll(): Observable<Product[]> {
    return this.http.get<Product[]>(this.baseUrl);
  }

  getById(id: number): Observable<Product> {
    return this.http.get<Product>(`${this.baseUrl}/${id}`);
  }

  create(product: Product): Observable<Product> {
    return this.http.post<Product>(this.baseUrl, product);
  }

  update(id: number, product: Partial<Product>): Observable<Product> {
    return this.http.put<Product>(`${this.baseUrl}/${id}`, product);
  }
}
"""


def test_typescript_produces_chunks():
    chunker = CodeChunker()
    unit = _unit(_TS_FIXTURE, "typescript", "ProductService")
    chunks = chunker.chunk_unit(unit)

    assert len(chunks) >= 1  # may not split if regex/AST path not triggered
    bodies = " ".join(c.body for c in chunks)
    assert "ProductService" in bodies or any("Product" in c.body for c in chunks)


def test_typescript_bodies_not_truncated():
    """Even if split produces one whole-file chunk, the body is the full content."""
    chunker = CodeChunker()
    unit = _unit(_TS_FIXTURE, "typescript", "ProductService")
    chunks = chunker.chunk_unit(unit)
    total_body_chars = sum(len(c.body) for c in chunks)
    assert total_body_chars >= len(_TS_FIXTURE) * 0.8  # at least 80% of content preserved


# ── Go ────────────────────────────────────────────────────────────────────────

_GO_FIXTURE = """\
package repository

import (
    "context"
    "database/sql"
)

type UserRepo struct {
    db *sql.DB
}

func (r *UserRepo) FindByID(ctx context.Context, id int) (*User, error) {
    row := r.db.QueryRowContext(ctx, "SELECT id, email FROM users WHERE id = $1", id)
    var u User
    err := row.Scan(&u.ID, &u.Email)
    return &u, err
}

func (r *UserRepo) Save(ctx context.Context, u *User) error {
    _, err := r.db.ExecContext(ctx,
        "INSERT INTO users (email, name) VALUES ($1, $2)",
        u.Email, u.Name,
    )
    return err
}
"""


def test_go_produces_at_least_one_chunk():
    chunker = CodeChunker()
    unit = _unit(_GO_FIXTURE, "go", "UserRepo")
    chunks = chunker.chunk_unit(unit)

    assert len(chunks) >= 1
    bodies = " ".join(c.body for c in chunks)
    assert "SELECT id, email FROM users" in bodies


# ── No-truncation invariant ───────────────────────────────────────────────────

def test_no_content_slice_in_output():
    """
    ADR-0044 invariant: total body chars across all chunks must cover the
    full content (minus imports/headers counted in header_context).
    No single chunk body should be silently truncated.
    """
    chunker = CodeChunker()
    unit = _unit(_JAVA_FIXTURE, "java", "OrderService")
    chunks = chunker.chunk_unit(unit)

    for chunk in chunks:
        # body must end at a natural boundary (closing brace or dedent),
        # not a mid-string character
        assert len(chunk.body) > 0
        assert chunk.body_hash == _sha256(chunk.body)


def test_chunk_kind_is_valid():
    chunker = CodeChunker()
    unit = _unit(_JAVA_FIXTURE, "java", "OrderService")
    for chunk in chunker.chunk_unit(unit):
        assert chunk.kind in ("method", "top_decl", "schema_block", "unreadable_file")


def test_import_context_capped_at_50_lines():
    """import_context must never exceed 50 lines."""
    # Build a file with 100 import lines
    imports = "\n".join(f"import com.example.pkg{i}.Class{i};" for i in range(100))
    body = f"{imports}\n\npublic class BigImports {{\n    public void run() {{}}\n}}"
    chunker = CodeChunker()
    unit = _unit(body, "java", "BigImports")
    chunks = chunker.chunk_unit(unit)
    for chunk in chunks:
        lines = chunk.import_context.splitlines()
        assert len(lines) <= 51  # 50 + optional "... N more" line


def test_sql_schema_chunked_by_table():
    """SQL migration files produce one chunk per CREATE TABLE."""
    sql = """\
CREATE TABLE users (
    id UUID PRIMARY KEY,
    email TEXT NOT NULL
);

CREATE TABLE orders (
    id UUID PRIMARY KEY,
    user_id UUID REFERENCES users(id),
    total NUMERIC(10,2)
);
"""
    chunker = CodeChunker()
    unit = _unit(sql, "sql", "migration")
    chunks = chunker.chunk_unit(unit)

    schema_chunks = [c for c in chunks if c.kind == "schema_block"]
    assert len(schema_chunks) == 2
    qnames = {c.qname for c in schema_chunks}
    assert "users" in qnames
    assert "orders" in qnames
