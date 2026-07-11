import ast
from pathlib import Path


APP_DIR = Path(__file__).resolve().parents[1] / "tech_doc_agent" / "app"
CORE_DIR = APP_DIR / "core"
RUNTIME_DIR = APP_DIR / "runtime"
FORBIDDEN_CORE_DEPENDENCIES = (
    "tech_doc_agent.app.api",
    "tech_doc_agent.app.services",
)
FORBIDDEN_RUNTIME_DEPENDENCIES = (
    "tech_doc_agent.app.api",
    "tech_doc_agent.app.services",
)


def test_core_does_not_depend_on_api_or_services():
    assert _dependency_violations(CORE_DIR, FORBIDDEN_CORE_DEPENDENCIES) == []


def test_runtime_does_not_depend_on_api_or_legacy_services():
    assert _dependency_violations(RUNTIME_DIR, FORBIDDEN_RUNTIME_DEPENDENCIES) == []


def test_runtime_package_init_does_not_eagerly_load_components():
    path = RUNTIME_DIR / "__init__.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    imports = [
        imported_module
        for node in ast.walk(tree)
        for imported_module in _imported_modules(node)
    ]

    assert imports == []


def _dependency_violations(directory: Path, forbidden_prefixes: tuple[str, ...]) -> list[str]:
    violations: list[str] = []

    for path in sorted(directory.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            imported_modules = _imported_modules(node)
            for imported_module in imported_modules:
                if imported_module.startswith(forbidden_prefixes):
                    violations.append(f"{path.name}:{node.lineno} imports {imported_module}")

    return violations


def _imported_modules(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if isinstance(node, ast.ImportFrom) and node.module:
        return [node.module]
    return []
