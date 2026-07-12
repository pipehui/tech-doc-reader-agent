import ast
from pathlib import Path


APP_DIR = Path(__file__).resolve().parents[1] / "tech_doc_agent" / "app"
APPLICATION_DIR = APP_DIR / "application"
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


def test_application_use_cases_do_not_depend_on_adapters_or_delivery_layers():
    assert _dependency_violations(
        APPLICATION_DIR,
        (
            "tech_doc_agent.app.api",
            "tech_doc_agent.app.infrastructure",
            "tech_doc_agent.app.services",
            "tech_doc_agent.app.tools",
        ),
    ) == []


def test_learning_tools_delegate_writes_to_application_service():
    source = (TOOLS_DIR / "learning.py").read_text(encoding="utf-8")
    assert "learning_state_service.update(" in source
    assert "learning_store.upsert_record(" not in source
    assert "memory_store.upsert_memory(" not in source
    assert "learning_store.save()" not in source
    assert "memory_store.save()" not in source


def test_learning_state_uses_domain_models_until_delivery_serialization():
    state_source = (APPLICATION_DIR / "learning_state.py").read_text(encoding="utf-8")
    tool_source = (TOOLS_DIR / "learning.py").read_text(encoding="utf-8")
    api_source = (APP_DIR / "api" / "routes" / "learning.py").read_text(encoding="utf-8")

    assert "records: list[LearningRecord]" in state_source
    assert "memories: list[MemoryFragment]" in state_source
    assert "learning_store.query_records(" in tool_source
    assert "learning_store.list_records(" in tool_source
    assert "memory_store.query_memories(" in tool_source
    assert "learning_store.read_by_query(" not in tool_source
    assert "learning_store.read_overview(" not in tool_source
    assert "memory_store.read_by_query(" not in tool_source
    assert "learning_store.list_records(" in api_source
    assert "memory_store.query_memories(" in api_source


def test_profile_domain_and_service_stay_typed_until_delivery_serialization():
    model_source = (APPLICATION_DIR / "profile_models.py").read_text(encoding="utf-8")
    service_source = (APPLICATION_DIR / "profile_service.py").read_text(encoding="utf-8")
    tool_source = (TOOLS_DIR / "profiles.py").read_text(encoding="utf-8")
    api_source = (APP_DIR / "api" / "routes" / "learning.py").read_text(encoding="utf-8")
    resource_source = (APP_DIR / "services" / "resources.py").read_text(encoding="utf-8")

    assert "class UserProfile:" in model_source
    assert "class UserProfileUpdate:" in model_source
    assert "-> UserProfile" in service_source
    assert "-> UserProfileUpdateResult" in service_source
    assert "json.dumps(profile.to_payload()" in tool_source
    assert "**profile.to_payload()" in api_source
    assert "services.user_profile" not in resource_source


def test_approval_domain_does_not_live_under_runtime_or_leak_into_redis_adapter():
    runtime_source = (RUNTIME_DIR / "approvals.py").read_text(encoding="utf-8")
    repository_source = (
        APP_DIR / "infrastructure" / "persistence" / "approval_repository.py"
    ).read_text(encoding="utf-8")
    in_memory_source = (
        APP_DIR
        / "infrastructure"
        / "persistence"
        / "in_memory_approval_repository.py"
    ).read_text(encoding="utf-8")

    assert "class GuardrailApprovalRequest" not in runtime_source
    assert "class ApprovalRepository(Protocol)" not in runtime_source
    assert "class InMemoryApprovalRepository" not in runtime_source
    assert "tech_doc_agent.app.application.approval_models" in repository_source
    assert "tech_doc_agent.app.runtime" not in repository_source
    assert "tech_doc_agent.app.application.approval_models" in in_memory_source
    assert "tech_doc_agent.app.runtime" not in in_memory_source


def test_persistence_adapters_do_not_expose_unapproved_retention_deletion():
    paths = (
        APP_DIR / "infrastructure" / "persistence" / "generations.py",
        APP_DIR / "infrastructure" / "persistence" / "faiss_snapshot.py",
        APP_DIR / "infrastructure" / "persistence" / "learning_state_repository.py",
        APP_DIR / "infrastructure" / "persistence" / "user_profile_repository.py",
        APP_DIR / "infrastructure" / "persistence" / "legacy_migration.py",
    )
    forbidden_prefixes = ("delete", "prune", "purge", "gc", "retention")
    violations = []
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            for member in node.body:
                if (
                    isinstance(member, ast.FunctionDef)
                    and not member.name.startswith("_")
                    and member.name.casefold().startswith(forbidden_prefixes)
                ):
                    violations.append(f"{path.name}:{member.lineno} exposes {member.name}")

    assert violations == []


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


def test_role_modules_do_not_define_or_load_prompt_templates():
    forbidden_fragments = (
        "ChatPromptTemplate",
        "_assistant_prompt",
        "datetime.now",
        "build_prompt_registry",
    )
    violations = []
    for filename in (
        "primary_assistant.py",
        "parser_assistant.py",
        "relation_assistant.py",
        "explanation_assistant.py",
        "examination_assistant.py",
        "summary_assistant.py",
    ):
        source = (ASSISTANTS_DIR / filename).read_text(encoding="utf-8")
        for fragment in forbidden_fragments:
            if fragment in source:
                violations.append(f"{filename} contains {fragment}")

    assert violations == []


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


def test_graph_execution_policy_does_not_read_settings_at_runtime():
    assert _dependency_violations(
        APP_DIR / "graph",
        ("tech_doc_agent.app.core.settings",),
        filenames=(
            "builder.py",
            "reflection.py",
            "specs.py",
            "tool_nodes.py",
            "tool_policy.py",
        ),
    ) == []


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


def test_ambiguous_tenant_fallback_api_is_removed():
    violations = []
    for path in sorted(APP_DIR.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        if "tenant_from_values" in source:
            violations.append(str(path.relative_to(APP_DIR)))

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
