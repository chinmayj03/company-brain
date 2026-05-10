"""
ADR-0047 acceptance test: per-language chunk correctness.

Verifies that:
1. CodeChunker detects language correctly from file extension.
2. Each language produces chunks with the right `language` field set.
3. No Java-specific fallback bleeds into non-Java files.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from companybrain.pipeline.code_chunker import CodeChunker, _LANGUAGE_MAP


_PYTHON_SOURCE = '''\
class PaymentService:
    def charge(self, amount: float, card_token: str) -> bool:
        """Charge the given card via Stripe."""
        result = self._stripe.charge(amount, card_token)
        self._db.insert("payments", {"amount": amount, "token": card_token})
        return result.success

    def refund(self, payment_id: str) -> bool:
        payment = self._db.get("payments", payment_id)
        return self._stripe.refund(payment["token"])
'''

_TYPESCRIPT_SOURCE = '''\
export class UserRepository {
  constructor(private readonly db: Database) {}

  async findById(id: string): Promise<User | null> {
    return this.db.query<User>(`SELECT * FROM users WHERE id = $1`, [id]);
  }

  async save(user: User): Promise<void> {
    await this.db.execute(
      `INSERT INTO users (id, email) VALUES ($1, $2)`,
      [user.id, user.email]
    );
  }
}
'''

_GO_SOURCE = '''\
package repo

import "database/sql"

type OrderRepo struct {
    db *sql.DB
}

func (r *OrderRepo) FindByID(id int) (*Order, error) {
    row := r.db.QueryRow("SELECT id, total FROM orders WHERE id = $1", id)
    var o Order
    return &o, row.Scan(&o.ID, &o.Total)
}

func (r *OrderRepo) Insert(o *Order) error {
    _, err := r.db.Exec("INSERT INTO orders (total) VALUES ($1)", o.Total)
    return err
}
'''


def _write_tmp(content: str, suffix: str) -> str:
    f = tempfile.NamedTemporaryFile(suffix=suffix, mode="w", delete=False)
    f.write(content)
    f.close()
    return f.name


@pytest.mark.acceptance
@pytest.mark.parametrize("source,suffix,expected_lang,expected_min_chunks", [
    (_PYTHON_SOURCE,     ".py",  "python",     2),
    (_TYPESCRIPT_SOURCE, ".ts",  "typescript", 1),
    (_GO_SOURCE,         ".go",  "go",         2),
])
def test_language_detected_correctly(source, suffix, expected_lang, expected_min_chunks):
    tmp = _write_tmp(source, suffix)
    try:
        chunker = CodeChunker()
        chunks = chunker.chunk_file(tmp)
        assert len(chunks) >= expected_min_chunks, (
            f"Expected >= {expected_min_chunks} chunks for {suffix}, got {len(chunks)}"
        )
        for c in chunks:
            assert c.language == expected_lang, (
                f"Chunk {c.qname} has language={c.language!r}, expected {expected_lang!r}"
            )
    finally:
        Path(tmp).unlink(missing_ok=True)


@pytest.mark.acceptance
def test_non_java_never_returns_java_language():
    """No Python or TypeScript chunk should ever carry language='java'."""
    for source, suffix in [
        (_PYTHON_SOURCE, ".py"),
        (_TYPESCRIPT_SOURCE, ".ts"),
        (_GO_SOURCE, ".go"),
    ]:
        tmp = _write_tmp(source, suffix)
        try:
            chunks = CodeChunker().chunk_file(tmp)
            for c in chunks:
                assert c.language != "java", (
                    f"Chunk {c.qname} in {suffix} file has language='java' "
                    "— Java language is leaking into non-Java chunking"
                )
        finally:
            Path(tmp).unlink(missing_ok=True)


@pytest.mark.acceptance
def test_language_map_covers_common_extensions():
    """Verify the language map includes all extensions we claim to support."""
    expected = {".java", ".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".rb", ".sql"}
    missing = expected - set(_LANGUAGE_MAP.keys())
    assert not missing, f"Missing from _LANGUAGE_MAP: {missing}"
