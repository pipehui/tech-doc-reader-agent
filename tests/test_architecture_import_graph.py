from pathlib import Path

from tests.architecture.import_graph import DependencyContract, PythonImportGraph


def _write(root: Path, relative: str, source: str = "") -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def test_import_graph_scans_nested_modules_and_resolves_relative_imports(tmp_path):
    root = tmp_path / "demo" / "app"
    _write(root, "__init__.py")
    _write(root, "core/__init__.py")
    _write(root, "core/nested/__init__.py")
    _write(
        root,
        "core/nested/consumer.py",
        "\n".join(
            (
                "from ...services import backend",
                "from demo.app import services",
                "import demo.app.services.backend",
                "from . import local",
                "import json",
            )
        ),
    )
    _write(root, "core/nested/local.py")
    _write(root, "services/__init__.py")
    _write(root, "services/backend.py")

    graph = PythonImportGraph.build(root, package="demo.app")
    contract = DependencyContract(
        name="core isolation",
        source_prefixes=("demo.app.core",),
        forbidden_prefixes=("demo.app.services",),
    )

    assert contract.violations(graph) == [
        "core/nested/consumer.py:1 imports demo.app.services.backend",
        "core/nested/consumer.py:2 imports demo.app.services",
        "core/nested/consumer.py:3 imports demo.app.services.backend",
    ]


def test_dependency_contract_supports_explicit_composition_root_exclusions(tmp_path):
    root = tmp_path / "demo" / "app"
    _write(root, "__init__.py")
    _write(root, "core/__init__.py")
    _write(root, "core/value.py", "from demo.app.infrastructure import adapter")
    _write(root, "core/composition.py", "from demo.app.infrastructure import adapter")
    _write(root, "infrastructure/__init__.py")
    _write(root, "infrastructure/adapter.py")

    graph = PythonImportGraph.build(root, package="demo.app")
    contract = DependencyContract(
        name="core isolation",
        source_prefixes=("demo.app.core",),
        forbidden_prefixes=("demo.app.infrastructure",),
        excluded_importers=("demo.app.core.composition",),
    )

    assert contract.violations(graph) == [
        "core/value.py:1 imports demo.app.infrastructure.adapter"
    ]
