import ast
from pathlib import Path


CORE_DIR = Path(__file__).resolve().parents[1] / "tech_doc_agent" / "app" / "core"
FORBIDDEN_CORE_DEPENDENCIES = (
    "tech_doc_agent.app.api",
    "tech_doc_agent.app.services",
)


def test_core_does_not_depend_on_api_or_services():
    violations: list[str] = []

    for path in sorted(CORE_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            imported_modules = _imported_modules(node)
            for imported_module in imported_modules:
                if imported_module.startswith(FORBIDDEN_CORE_DEPENDENCIES):
                    violations.append(f"{path.name}:{node.lineno} imports {imported_module}")

    assert violations == []


def _imported_modules(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if isinstance(node, ast.ImportFrom) and node.module:
        return [node.module]
    return []
