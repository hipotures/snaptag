from pathlib import Path


def test_required_docs_exist() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    assert (repo_root / "README.md").exists()
    assert (repo_root / "docs/architecture/backend-runtime.md").exists()
    assert (repo_root / "docs/adr/0001-canonical-vs-derived-state.md").exists()
