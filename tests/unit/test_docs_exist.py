from pathlib import Path


def test_required_docs_exist() -> None:
    assert Path("README.md").exists()
    assert Path("docs/architecture/backend-runtime.md").exists()
