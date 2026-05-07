from pathlib import Path
from companybrain.cli_helpers.repo_walker import walk_repo


def test_skips_node_modules(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "foo.ts").write_text("export const x = 1;")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "lib.js").write_text("module.exports = {};")

    files = list(walk_repo(tmp_path))
    # Check relative to tmp_path so pytest tmp dir name doesn't interfere
    rel_files = [f.relative_to(tmp_path) for f in files]
    assert Path("src/foo.ts") in rel_files
    assert not any("node_modules" in p.parts for p in rel_files)


def test_skips_git_dir(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("[core]")
    (tmp_path / "main.py").write_text("x = 1")

    files = list(walk_repo(tmp_path))
    assert any("main.py" in str(f) for f in files)
    assert not any(".git" in str(f) for f in files)


def test_skips_build_dirs(tmp_path):
    for skip_dir in ["target", "dist", "build", ".venv", "__pycache__", ".brain"]:
        d = tmp_path / skip_dir
        d.mkdir()
        (d / "artifact.java").write_text("// generated")

    (tmp_path / "App.java").write_text("class App {}")
    files = list(walk_repo(tmp_path))
    rel_files = [f.relative_to(tmp_path) for f in files]
    assert Path("App.java") in rel_files
    for skip_dir in ["target", "dist", "build", ".venv", "__pycache__", ".brain"]:
        assert not any(skip_dir in p.parts for p in rel_files)


def test_returns_only_source_files(tmp_path):
    (tmp_path / "README.md").write_text("# readme")
    (tmp_path / "config.json").write_text("{}")
    (tmp_path / "app.py").write_text("x = 1")
    (tmp_path / "Main.java").write_text("class Main {}")

    files = list(walk_repo(tmp_path))
    names = {f.name for f in files}
    assert "app.py" in names
    assert "Main.java" in names
    assert "README.md" not in names
    assert "config.json" not in names
