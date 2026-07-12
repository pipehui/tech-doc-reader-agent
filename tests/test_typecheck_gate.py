from pathlib import Path
import tomllib


ROOT = Path(__file__).resolve().parents[1]
MYPY_COMMAND = "mypy tech_doc_agent/app evals"


def test_mypy_checks_untyped_function_bodies():
    with (ROOT / "pyproject.toml").open("rb") as file:
        config = tomllib.load(file)

    assert config["tool"]["mypy"]["check_untyped_defs"] is True


def test_ci_and_development_docs_use_the_full_app_and_eval_scope():
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    development = (ROOT / "docs" / "development.md").read_text(encoding="utf-8")

    assert f"run: {MYPY_COMMAND}" in workflow
    assert f"python -m {MYPY_COMMAND}" in development
    assert "mypy tech_doc_agent/app/core tech_doc_agent/app/api/schemas.py" not in workflow
