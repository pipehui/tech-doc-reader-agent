import ast
from pathlib import Path


APP_DIR = Path(__file__).resolve().parents[1] / "tech_doc_agent" / "app"
CORE_DIR = APP_DIR / "core"
RUNTIME_DIR = APP_DIR / "runtime"
TOOLS_DIR = APP_DIR / "tools"
ASSISTANTS_DIR = APP_DIR / "services" / "assistants"
RETRIEVAL_DIR = APP_DIR / "services" / "retrieval"
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


def test_assistants_package_init_does_not_eagerly_load_role_definitions():
    path = ASSISTANTS_DIR / "__init__.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    imports = [
        imported_module
        for node in ast.walk(tree)
        for imported_module in _imported_modules(node)
    ]

    assert imports == []


def test_chat_model_construction_is_isolated_to_model_factory():
    violations = []
    for path in sorted(ASSISTANTS_DIR.glob("*.py")):
        if path.name == "model_factory.py":
            continue
        source = path.read_text(encoding="utf-8")
        if "ChatOpenAI" in source:
            violations.append(path.name)

    assert violations == []


def test_assistant_base_does_not_import_settings_or_model_factory():
    assert _dependency_violations(
        ASSISTANTS_DIR,
        (
            "langchain_openai",
            "tech_doc_agent.app.core.settings",
            "tech_doc_agent.app.services.assistants.model_factory",
        ),
        filenames=("assistant_base.py",),
    ) == []


def test_graph_builder_does_not_import_concrete_assistants_or_tools():
    violations = _dependency_violations(
        APP_DIR / "graph",
        (
            "tech_doc_agent.app.services.assistants",
            "tech_doc_agent.app.services.tools",
        ),
        filenames=("builder.py",),
    )

    assert violations == []


def test_dependency_bound_tools_do_not_use_legacy_resource_locator():
    assert _dependency_violations(
        TOOLS_DIR,
        (
            "tech_doc_agent.app.services.resources",
            "tech_doc_agent.app.services.tools",
            "tech_doc_agent.app.services.user_profile",
        ),
    ) == []


def test_global_app_resource_locator_symbols_are_removed():
    forbidden_symbols = (
        "get_app_resources",
        "set_app_resources",
        "reset_app_resources",
        "override_app_resources",
        "_current_resources",
    )
    violations = []

    for path in sorted(APP_DIR.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        for symbol in forbidden_symbols:
            if symbol in source:
                violations.append(f"{path.relative_to(APP_DIR)} contains {symbol}")

    assert violations == []


def test_retrieval_metadata_helpers_follow_one_way_dependency_direction():
    package = "tech_doc_agent.app.services.retrieval"
    assert _dependency_violations(
        RETRIEVAL_DIR,
        (f"{package}.inference", f"{package}.normalization", f"{package}.filters"),
        filenames=("taxonomy.py",),
    ) == []
    assert _dependency_violations(
        RETRIEVAL_DIR,
        (f"{package}.normalization", f"{package}.filters"),
        filenames=("inference.py",),
    ) == []
    assert _dependency_violations(
        RETRIEVAL_DIR,
        (f"{package}.filters",),
        filenames=("normalization.py",),
    ) == []


def test_extracted_retrieval_components_do_not_depend_on_hybrid_or_settings():
    assert _dependency_violations(
        RETRIEVAL_DIR,
        (
            "tech_doc_agent.app.core.settings",
            "tech_doc_agent.app.services.retrieval.hybrid",
        ),
        filenames=(
            "bm25.py",
            "documents.py",
            "exact.py",
            "formatting.py",
            "fusion.py",
            "models.py",
            "semantic.py",
            "tokenization.py",
        ),
    ) == []


def _dependency_violations(
    directory: Path,
    forbidden_prefixes: tuple[str, ...],
    *,
    filenames: tuple[str, ...] | None = None,
) -> list[str]:
    violations: list[str] = []

    paths = (
        (directory / filename for filename in filenames)
        if filenames is not None
        else directory.glob("*.py")
    )
    for path in sorted(paths):
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
