from pathlib import Path
from companybrain.cli_helpers.endpoint_discovery import discover_endpoints


def test_finds_java_spring_endpoints(tmp_path):
    (tmp_path / "Foo.java").write_text(
        '@RestController class Foo {\n'
        '  @GetMapping("/users/{id}") public X getById() { return null; }\n'
        '  @PostMapping("/users")     public X create() { return null; }\n'
        '}\n'
    )
    eps = discover_endpoints(tmp_path)
    assert ("GET",  "/users/{id}") in eps
    assert ("POST", "/users") in eps


def test_finds_fastapi_endpoints(tmp_path):
    (tmp_path / "main.py").write_text(
        'from fastapi import APIRouter\n'
        'router = APIRouter()\n'
        '@router.get("/health")\n'
        'def h(): return {}\n'
    )
    eps = discover_endpoints(tmp_path)
    assert ("GET", "/health") in eps


def test_finds_flask_style_endpoints(tmp_path):
    (tmp_path / "app.py").write_text(
        'from flask import Flask\n'
        'app = Flask(__name__)\n'
        '@app.post("/items")\n'
        'def create_item(): return {}\n'
    )
    eps = discover_endpoints(tmp_path)
    assert ("POST", "/items") in eps


def test_deduplicates_endpoints(tmp_path):
    java = (
        '@RestController class A {\n'
        '  @GetMapping("/ping") public String ping() { return ""; }\n'
        '}\n'
    )
    (tmp_path / "A.java").write_text(java)
    (tmp_path / "B.java").write_text(java)
    eps = discover_endpoints(tmp_path)
    assert eps.count(("GET", "/ping")) == 1


def test_skips_build_dirs(tmp_path):
    (tmp_path / "target").mkdir()
    (tmp_path / "target" / "Gen.java").write_text(
        '@GetMapping("/generated") public X g() {}\n'
    )
    (tmp_path / "Real.java").write_text(
        '@GetMapping("/real") public X r() {}\n'
    )
    eps = discover_endpoints(tmp_path)
    paths = [e[1] for e in eps]
    assert "/real" in paths
    assert "/generated" not in paths


def test_returns_empty_for_no_sources(tmp_path):
    (tmp_path / "README.md").write_text("# readme")
    assert discover_endpoints(tmp_path) == []
