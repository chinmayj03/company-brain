"""
Code navigation tools — existing regex/AST helpers promoted to agent-callable tools.

Each function is a pure Python callable that the agent can invoke via the tool
registry. They do the cheap, deterministic work so the LLM only has to reason
about WHAT to call, not HOW to scan files.

Tools available here:
  read_file              — read any source file
  find_file_by_name      — locate a class/module file in a repo
  extract_method         — pull one method body (brace-balanced)
  get_class_fields       — list injected/autowired dependencies
  get_imports            — list import statements
  find_implementations   — find concrete class implementing an interface
  search_codebase        — grep-style keyword search across a repo
  list_methods           — list all method signatures in a file
  find_entry_handler     — find the HTTP handler for an endpoint
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Optional

import structlog

log = structlog.get_logger(__name__)

# ── Skip dirs ─────────────────────────────────────────────────────────────────

_SKIP_DIRS = frozenset({
    "node_modules", ".git", "dist", "build", "generated",
    "target", "__pycache__", ".gradle", ".idea", ".vscode",
})

# ── Regex helpers (reused from code_tracer / smart_method_extractor) ──────────

_JAVA_IMPORT_RE    = re.compile(r'^import\s+([\w.]+);', re.MULTILINE)
_JAVA_CLASS_RE     = re.compile(r'(?:public\s+)?(?:abstract\s+)?(?:class|interface|enum)\s+(\w+)')
_JAVA_MAPPING_RE   = re.compile(
    r'@(RequestMapping|GetMapping|PostMapping|PutMapping|DeleteMapping|PatchMapping)'
    r'\s*\(?[^)]*?(?:value\s*=\s*)?\{?\s*["\']([^"\']+)["\']',
    re.MULTILINE,
)
_FIELD_RE = re.compile(
    r'(?:@(?:Autowired|Inject|Resource)\s*(?:\([^)]*\))?\s*)?'
    r'(?:private|protected|public)\s+(?:final\s+)?'
    r'([\w<>]+)\s+(\w+)\s*;',
    re.MULTILINE,
)
_IMPLEMENTS_RE = re.compile(
    r'\bimplements\b[^{]*\b(\w+)\b',
    re.MULTILINE,
)


# ── Tool functions ─────────────────────────────────────────────────────────────

def read_file(path: str, max_chars: int = 8000) -> str:
    """
    Read a source file and return its content (capped at max_chars).
    Returns an error message string if the file cannot be read.
    """
    try:
        content = Path(path).read_text(encoding="utf-8", errors="replace")
        if len(content) > max_chars:
            content = content[:max_chars] + f"\n// ... (truncated at {max_chars} chars)"
        return content
    except Exception as e:
        return f"ERROR: could not read {path}: {e}"


def find_file_by_name(class_name: str, repo_path: str,
                      extension: str = ".java") -> list[str]:
    """
    Find files matching a class/module name in a repository.
    Returns a list of absolute file paths (empty if not found).
    Searches by filename (ClassName.java) first, then by rglob.
    """
    repo = Path(repo_path)
    results: list[str] = []

    for f in repo.rglob(f"*{class_name}{extension}"):
        if any(skip in f.parts for skip in _SKIP_DIRS):
            continue
        if f.stem == class_name or f.stem.endswith(class_name):
            results.append(str(f))

    log.debug("find_file_by_name", class_name=class_name, found=len(results))
    return results


def extract_method(file_path: str, method_name: str) -> str:
    """
    Extract a single method body from a Java source file by method name.
    Uses brace-balanced extraction — returns the annotations + signature + body.
    Returns empty string if the method is not found.
    """
    try:
        content = Path(file_path).read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return f"ERROR: {e}"

    # Find the method signature
    pattern = re.compile(
        rf'(?:(?:public|private|protected|default|abstract|static|final|synchronized|override)\s+)*'
        rf'[\w<>\[\]?,\s]+\s+{re.escape(method_name)}\s*\(',
        re.MULTILINE,
    )
    match = pattern.search(content)
    if not match:
        return ""

    # Collect annotations immediately above (consecutive @... lines)
    ann_start = _find_annotation_start(content, match.start())
    header_start = ann_start

    # Find opening brace
    open_brace = content.find("{", match.start())
    if open_brace == -1:
        # Interface / abstract method — return signature only
        sig_end = content.find(";", match.start())
        return content[header_start:sig_end + 1].strip() if sig_end != -1 else ""

    # Brace-balanced walk
    body_start = open_brace + 1
    depth = 1
    i = body_start
    while i < len(content) and depth > 0:
        ch = content[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        elif ch in ('"', "'"):
            q = ch; i += 1
            while i < len(content) and content[i] != q:
                if content[i] == "\\": i += 1
                i += 1
        elif content[i:i+2] == "//":
            nl = content.find("\n", i)
            i = nl if nl != -1 else len(content)
        i += 1

    body = content[body_start:i - 1].strip()
    header = content[header_start:open_brace].strip()

    # Trim body if very long
    if len(body) > 1200:
        cut = body.rfind("\n", 0, 1200)
        body = body[:cut] + "\n    // ... (truncated)" if cut > 600 else body[:1200] + " // ..."

    return f"{header} {{\n{body}\n}}"


def get_class_fields(file_path: str) -> list[dict]:
    """
    List fields (especially @Autowired / constructor-injected) in a Java class.
    Returns: [{"type": "SomeService", "name": "someService"}, ...]
    """
    try:
        content = Path(file_path).read_text(encoding="utf-8", errors="replace")
    except Exception:
        return []

    fields = []
    seen = set()
    for m in _FIELD_RE.finditer(content):
        typ, name = m.group(1), m.group(2)
        if name not in seen and not typ.startswith(("boolean", "int", "long", "String", "List", "Map")):
            seen.add(name)
            fields.append({"type": typ, "name": name})
    return fields


def get_imports(file_path: str) -> list[str]:
    """
    Return the import statements from a Java file (fully-qualified class names).
    """
    try:
        content = Path(file_path).read_text(encoding="utf-8", errors="replace")
    except Exception:
        return []
    return _JAVA_IMPORT_RE.findall(content)


def find_implementations(interface_name: str, repo_path: str) -> list[str]:
    """
    Find Java classes that implement a given interface.
    Returns a list of file paths, ranked: adapter/db paths first, then others.
    Skips files that are themselves interfaces.
    """
    repo = Path(repo_path)
    impl_re = re.compile(
        rf'\bimplements\b[^{{]*\b{re.escape(interface_name)}\b',
        re.MULTILINE,
    )
    candidates: list[tuple[int, str]] = []

    for java_file in repo.rglob("*.java"):
        if any(skip in java_file.parts for skip in _SKIP_DIRS):
            continue
        try:
            content = java_file.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if not impl_re.search(content):
            continue
        if re.search(r'\bpublic\s+interface\b', content[:500]):
            continue  # skip intermediate interfaces

        priority = 0
        lparts = [p.lower() for p in java_file.parts]
        if any(p in lparts for p in ("adapter", "adapters", "infrastructure", "impl", "db", "jpa")):
            priority += 10
        if any(ann in content for ann in ("@Repository", "@Component", "@Service")):
            priority += 5

        candidates.append((priority, str(java_file)))

    candidates.sort(key=lambda x: -x[0])
    return [path for _, path in candidates]


def extract_db_queries(file_path: str) -> list[dict]:
    """
    Extract ALL database query patterns from a Java file.

    Handles:
      JPA/Spring Data
        - @Query("SELECT ...") / nativeQuery=true
        - EntityManager.createQuery / createNativeQuery
        - Spring Data derived methods (findBy..., countBy..., etc.)

      jOOQ (DSL-based, programmatic SQL)
        - dslContext.select(...).from(TABLE).where(...)
        - DSL.using(...).select(...)
        - Any variable named dsl/ctx/context/create with .select/.insertInto/.update/.deleteFrom
        - .fetch() / .fetchInto() / .fetchOne() / .execute() terminal calls
        - Extracts table names from .from(TABLE), .join(TABLE), .insertInto(TABLE), etc.
        - Extracts column names from .select(FIELD), .where(FIELD.eq(...)), etc.

      Spring JDBC / JdbcTemplate
        - jdbcTemplate.query("SELECT ...")
        - jdbcTemplate.update("INSERT/UPDATE/DELETE ...")
        - namedParameterJdbcTemplate.query/update(...)
        - jdbcTemplate.queryForObject / queryForList

      Raw JDBC
        - connection.prepareStatement("SELECT ...")
        - connection.createStatement().execute("...")

      MyBatis
        - @Select / @Insert / @Update / @Delete annotations

    Returns: [
      {
        "method":    "findPayerCompetitors",   # enclosing Java method name
        "query":     "SELECT ...",             # raw SQL/JPQL, or jOOQ DSL summary
        "type":      "jooq|native_sql|jpql|derived|jdbc|mybatis",
        "tables":    ["COMPETITOR", "PAYER"],  # tables detected in jOOQ / SQL
        "columns":   ["COMPETITOR.ID", ...],   # columns detected
        "operation": "SELECT|INSERT|UPDATE|DELETE",
        "is_native": True/False
      }
    ]
    """
    try:
        content = Path(file_path).read_text(encoding="utf-8", errors="replace")
    except Exception:
        return []

    results: list[dict] = []
    existing_methods: set[str] = set()

    # ── Helper: find the enclosing method name for a match position ───────────
    def _enclosing_method(pos: int) -> str:
        """Walk backwards from pos to find the nearest method signature."""
        snippet = content[:pos]
        m = re.search(
            r'(?:public|private|protected)\s+[\w<>?,\s\[\]]+\s+(\w+)\s*\([^)]*\)\s*(?:throws[^{]*)?\s*\{[^{]*$',
            snippet,
        )
        return m.group(1) if m else ""

    # ── 1. @Query annotation (JPA/Spring Data) ────────────────────────────────
    query_ann_re = re.compile(
        r'@Query\s*\(\s*(?:value\s*=\s*)?["\']([^"\']+)["\']'
        r'(?:[^)]*?nativeQuery\s*=\s*(true|false))?[^)]*\)\s*'
        r'(?:.*?\n\s*)?(?:public\s+)?[\w<>?,\s\[\]]+\s+(\w+)\s*\(',
        re.MULTILINE | re.DOTALL,
    )
    for m in query_ann_re.finditer(content):
        sql = m.group(1).strip()
        is_native = (m.group(2) or "false").lower() == "true"
        method = m.group(3)
        results.append({
            "method": method, "query": sql,
            "type": "native_sql" if is_native else "jpql",
            "tables": _tables_from_sql(sql), "columns": _columns_from_sql(sql),
            "operation": _operation_from_sql(sql), "is_native": is_native,
        })
        existing_methods.add(method)

    # ── 2. EntityManager.createQuery / createNativeQuery ─────────────────────
    em_re = re.compile(
        r'create(Native)?Query\s*\(\s*"([^"]{8,})"',
        re.MULTILINE,
    )
    for m in em_re.finditer(content):
        is_native = m.group(1) == "Native"
        sql = m.group(2).strip()
        method = _enclosing_method(m.start())
        results.append({
            "method": method, "query": sql,
            "type": "native_sql" if is_native else "jpql",
            "tables": _tables_from_sql(sql), "columns": _columns_from_sql(sql),
            "operation": _operation_from_sql(sql), "is_native": is_native,
        })

    # ── 3. MyBatis @Select / @Insert / @Update / @Delete annotations ─────────
    mybatis_re = re.compile(
        r'@(Select|Insert|Update|Delete)\s*\(\s*["\']([^"\']{8,})["\']',
        re.MULTILINE,
    )
    for m in mybatis_re.finditer(content):
        op, sql = m.group(1).upper(), m.group(2).strip()
        method = _enclosing_method(m.start())
        results.append({
            "method": method, "query": sql,
            "type": "mybatis", "tables": _tables_from_sql(sql),
            "columns": _columns_from_sql(sql), "operation": op, "is_native": True,
        })

    # ── 4. Spring JdbcTemplate / NamedParameterJdbcTemplate ──────────────────
    jdbc_re = re.compile(
        r'(?:jdbcTemplate|namedParameterJdbcTemplate|jdbc)\s*\.\s*'
        r'(?:query|update|execute|queryForObject|queryForList|batchUpdate)\s*\(\s*"([^"]{8,})"',
        re.MULTILINE,
    )
    for m in jdbc_re.finditer(content):
        sql = m.group(1).strip()
        method = _enclosing_method(m.start())
        results.append({
            "method": method, "query": sql,
            "type": "jdbc", "tables": _tables_from_sql(sql),
            "columns": _columns_from_sql(sql), "operation": _operation_from_sql(sql),
            "is_native": True,
        })

    # ── 5. jOOQ DSL chains ───────────────────────────────────────────────────
    # Strategy: find every terminal jOOQ call (.fetch / .execute / .fetchOne /
    # .fetchInto / .fetchMap / .store / .insert / .merge) then walk backward to
    # reconstruct the DSL chain and extract table + column names from it.
    jooq_terminal_re = re.compile(
        r'\.(?:fetch|fetchOne|fetchInto|fetchSingle|fetchAny|fetchMap|'
        r'fetchGroups|fetchArray|execute|store|insert|merge|delete)\s*\(',
        re.MULTILINE,
    )
    # Also detect jOOQ operation starters for operation type
    jooq_op_re = re.compile(
        r'\.(select|selectDistinct|selectFrom|selectCount|insertInto|update|deleteFrom|mergeInto)\s*\(',
        re.IGNORECASE,
    )
    for m in jooq_terminal_re.finditer(content):
        # Walk back ~800 chars to capture the chain start
        chain_start = max(0, m.start() - 800)
        chain = content[chain_start: m.end()]

        # Only process if it looks like a jOOQ chain
        if not re.search(r'\b(?:dsl|DSL|ctx|create|context|jooq)\b', chain, re.IGNORECASE):
            # also accept .select( / .insertInto( as chain start evidence
            if not jooq_op_re.search(chain):
                continue

        tables  = _jooq_tables(chain)
        columns = _jooq_columns(chain)
        op_m    = jooq_op_re.search(chain)
        op = op_m.group(1).upper().replace("FROM", "").replace("DISTINCT", "").replace("INTO", "").strip() if op_m else "SELECT"
        if op.startswith("DELETE"):
            op = "DELETE"
        elif op.startswith("INSERT"):
            op = "INSERT"
        elif op.startswith("UPDATE"):
            op = "UPDATE"
        elif op.startswith("MERGE"):
            op = "MERGE"
        else:
            op = "SELECT"

        method = _enclosing_method(m.start())

        # Build a human-readable query summary for the pipeline
        query_summary = f"jOOQ {op}"
        if tables:
            query_summary += f" on {', '.join(tables[:4])}"
        if columns:
            query_summary += f" — cols: {', '.join(columns[:6])}"

        # Deduplicate by (method, op, tables) — a single method may have one chain
        dedup_key = f"{method}:{op}:{','.join(sorted(tables))}"
        if not any(r.get("_dedup") == dedup_key for r in results):
            results.append({
                "method": method, "query": query_summary,
                "type": "jooq", "tables": tables, "columns": columns,
                "operation": op, "is_native": True, "_dedup": dedup_key,
            })

    # ── 6. Spring Data derived method names (findBy..., countBy..., etc.) ────
    derived_re = re.compile(
        r'(?:public\s+)?[\w<>?,\s\[\]]+\s+'
        r'((?:find|count|exists|delete|remove|sum|avg|min|max)By\w+)'
        r'\s*\(',
        re.MULTILINE,
    )
    for m in derived_re.finditer(content):
        method_name = m.group(1)
        if method_name not in existing_methods:
            results.append({
                "method": method_name, "query": f"Derived: {method_name}",
                "type": "derived", "tables": [], "columns": [],
                "operation": "SELECT", "is_native": False,
            })
            existing_methods.add(method_name)

    # Strip internal dedup keys before returning
    for r in results:
        r.pop("_dedup", None)

    log.debug("extract_db_queries", file=file_path, found=len(results))
    return results


# Keep old name as alias so existing callers don't break
extract_jpa_queries = extract_db_queries


# ── SQL / jOOQ analysis helpers ───────────────────────────────────────────────

def _operation_from_sql(sql: str) -> str:
    s = sql.strip().upper()
    for op in ("SELECT", "INSERT", "UPDATE", "DELETE", "MERGE", "WITH"):
        if s.startswith(op):
            return op
    return "SELECT"


def _tables_from_sql(sql: str) -> list[str]:
    """Extract table names from a SQL/JPQL string."""
    # FROM table / JOIN table / INTO table / UPDATE table
    pattern = re.compile(
        r'\b(?:FROM|JOIN|INTO|UPDATE)\s+([\w.]+)',
        re.IGNORECASE,
    )
    tables = []
    seen: set[str] = set()
    for m in pattern.finditer(sql):
        t = m.group(1).split(".")[-1].upper()  # strip schema prefix
        if t not in seen and not t.startswith(":") and len(t) > 1:
            tables.append(t)
            seen.add(t)
    return tables


def _columns_from_sql(sql: str) -> list[str]:
    """
    Extract column references from SQL/JPQL.
    Looks for table.column patterns and SELECT col1, col2 lists.
    """
    cols: list[str] = []
    seen: set[str] = set()
    # table.column references
    for m in re.finditer(r'\b(\w+)\.(\w+)\b', sql):
        col = f"{m.group(1).upper()}.{m.group(2).upper()}"
        if col not in seen:
            cols.append(col)
            seen.add(col)
    return cols[:12]  # cap


def _jooq_tables(chain: str) -> list[str]:
    """
    Extract table names from a jOOQ DSL chain.
    jOOQ typically uses SCREAMING_SNAKE_CASE constants for table names.
    Looks for: .from(TABLE), .join(TABLE), .insertInto(TABLE),
               .update(TABLE), .deleteFrom(TABLE)
    """
    pattern = re.compile(
        r'\.(?:from|join|leftJoin|rightJoin|innerJoin|crossJoin|insertInto|update|deleteFrom|mergeInto)\s*\(\s*(\w+)',
        re.IGNORECASE,
    )
    tables: list[str] = []
    seen: set[str] = set()
    for m in pattern.finditer(chain):
        t = m.group(1)
        # jOOQ table constants are typically uppercase or PascalCase
        # Skip obvious non-table names (subqueries, DSL refs, etc.)
        if t.upper() in ("DSL", "CTX", "DSL_CONTEXT", "CREATE") or len(t) < 2:
            continue
        key = t.upper()
        if key not in seen:
            tables.append(t)
            seen.add(key)
    return tables


def _jooq_columns(chain: str) -> list[str]:
    """
    Extract field/column references from a jOOQ DSL chain.
    Looks for TABLE.FIELD_NAME patterns and .select(F1, F2, ...) args.
    jOOQ uses TABLE.FIELD_NAME or just FIELD_NAME as constants.
    """
    cols: list[str] = []
    seen: set[str] = set()

    # TABLE.FIELD_NAME pattern (most common in jOOQ)
    for m in re.finditer(r'\b([A-Z_][A-Z0-9_]*)\.([A-Z_][A-Z0-9_]+)\b', chain):
        tbl, col = m.group(1), m.group(2)
        if tbl in ("DSL", "CTX", "CREATE") or len(col) < 2:
            continue
        ref = f"{tbl}.{col}"
        if ref not in seen:
            cols.append(ref)
            seen.add(ref)

    return cols[:12]


def search_codebase(keyword: str, repo_path: str,
                    file_extension: str = ".java",
                    max_results: int = 20) -> list[dict]:
    """
    Search for a keyword across the codebase using ripgrep (or plain grep).
    Returns: [{"file": "...", "line": 42, "text": "..."}, ...]
    """
    repo = Path(repo_path)
    results: list[dict] = []

    try:
        cmd = [
            "rg", "--line-number", "--no-heading",
            f"--glob=*{file_extension}",
            "--max-count=3",
            keyword, str(repo),
        ]
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        for line in out.stdout.splitlines()[:max_results]:
            parts = line.split(":", 2)
            if len(parts) >= 3:
                results.append({"file": parts[0], "line": int(parts[1]), "text": parts[2].strip()})
    except FileNotFoundError:
        # Fall back to Python glob + re
        pattern = re.compile(re.escape(keyword), re.IGNORECASE)
        for f in repo.rglob(f"*{file_extension}"):
            if any(skip in f.parts for skip in _SKIP_DIRS):
                continue
            try:
                for i, line in enumerate(f.read_text(errors="ignore").splitlines(), 1):
                    if pattern.search(line):
                        results.append({"file": str(f), "line": i, "text": line.strip()})
                        if len(results) >= max_results:
                            return results
            except Exception:
                continue
    except Exception as e:
        log.warning("search_codebase failed", error=str(e))

    return results


def list_methods(file_path: str) -> list[dict]:
    """
    List all method signatures in a Java file.
    Returns: [{"name": "getPayerCompetitors", "annotations": ["@Transactional"], "signature": "..."}]
    """
    try:
        content = Path(file_path).read_text(encoding="utf-8", errors="replace")
    except Exception:
        return []

    # Match method signatures with their preceding annotations
    method_re = re.compile(
        r'(?:@\w+[^\n]*\n\s*)*'         # optional annotations
        r'(?:public|private|protected)\s+'
        r'(?:(?:static|final|abstract|synchronized|default)\s+)*'
        r'([\w<>\[\]?,\s]+)\s+'          # return type
        r'(\w+)\s*\([^)]*\)',            # method name + params
        re.MULTILINE,
    )
    methods = []
    for m in method_re.finditer(content):
        name = m.group(2)
        if name in ("class", "interface", "if", "for", "while", "switch"):
            continue
        # Extract annotations from the matched block
        block = m.group(0)
        annotations = re.findall(r'@\w+(?:\([^)]*\))?', block)
        methods.append({
            "name": name,
            "annotations": annotations,
            "signature": f"{m.group(1).strip()} {name}(...)",
        })
    return methods


def find_entry_handler(endpoint: str, http_method: str, repo_path: str) -> dict:
    """
    Find the Spring Boot (or FastAPI/Express) handler for an endpoint.
    Returns: {"file": "...", "class": "...", "method": "...", "matched_path": "..."}
    or {} if not found.
    """
    repo = Path(repo_path)
    parts = [p for p in endpoint.split("/") if p and not re.match(r'^v\d+$', p) and p != "api"]
    candidates_lower = {("/" + "/".join(parts[i:])).lower().rstrip("/") for i in range(len(parts))}
    candidates_lower.add(endpoint.lower().rstrip("/"))

    # Java Spring Boot
    for java_file in repo.rglob("*.java"):
        if any(skip in java_file.parts for skip in _SKIP_DIRS):
            continue
        try:
            content = java_file.read_text(errors="ignore")
        except OSError:
            continue
        if "@RestController" not in content and "@Controller" not in content:
            continue

        class_path = ""
        first_method_pos = _first_method_pos(content)
        for match in _JAVA_MAPPING_RE.finditer(content):
            ann, path_val = match.group(1), match.group(2)
            if match.start() < first_method_pos:
                class_path = path_val.rstrip("/")
            else:
                full = (class_path + "/" + path_val.lstrip("/")).rstrip("/").lower()
                if full in candidates_lower or _tail_matches(full, candidates_lower):
                    method_name = _method_name_after(content, match.end())
                    class_name = _JAVA_CLASS_RE.search(content)
                    return {
                        "file": str(java_file),
                        "class": class_name.group(1) if class_name else "",
                        "method": method_name,
                        "matched_path": class_path + "/" + path_val.lstrip("/"),
                    }

    # Python FastAPI / Flask
    py_route_re = re.compile(
        r'@(?:router|app)\.(get|post|put|delete|patch)\s*\(\s*["\']([^"\']+)["\']',
        re.MULTILINE,
    )
    for py_file in repo.rglob("*.py"):
        if any(skip in py_file.parts for skip in _SKIP_DIRS):
            continue
        try:
            content = py_file.read_text(errors="ignore")
        except OSError:
            continue
        for m in py_route_re.finditer(content):
            if m.group(2).lower() in candidates_lower:
                return {"file": str(py_file), "class": "", "method": "", "matched_path": m.group(2)}

    return {}


# ── Internal helpers ───────────────────────────────────────────────────────────

def _find_annotation_start(content: str, pos: int) -> int:
    """Walk backwards to include consecutive @Annotation lines before pos."""
    lines = content[:pos].split("\n")
    start = pos
    for line in reversed(lines):
        stripped = line.strip()
        if re.match(r'@\w+(\s*\([^)]*\))?\s*$', stripped):
            start -= len(line) + 1
        else:
            break
    return max(0, start)


def _first_method_pos(content: str) -> int:
    m = re.search(r'(?:public|protected|private)\s+[\w<>?,\s\[\]]+\s+\w+\s*\(', content)
    return m.start() if m else len(content)


def _tail_matches(full_path: str, candidates: set[str]) -> bool:
    parts = full_path.strip("/").split("/")
    for i in range(len(parts)):
        tail = "/" + "/".join(parts[i:])
        if tail in candidates:
            return True
    return False


def _method_name_after(content: str, pos: int) -> str:
    """Extract the method name from the first method signature after pos."""
    m = re.search(
        r'(?:public|private|protected)\s+[\w<>?,\s\[\]]+\s+(\w+)\s*\(',
        content[pos:pos + 300],
    )
    return m.group(1) if m else ""
