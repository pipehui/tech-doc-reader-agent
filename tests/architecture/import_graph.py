from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


@dataclass(frozen=True, slots=True)
class ImportEdge:
    importer: str
    imported: str
    path: PurePosixPath
    line: int

    def describe(self) -> str:
        return f"{self.path}:{self.line} imports {self.imported}"


@dataclass(frozen=True, slots=True)
class DependencyContract:
    name: str
    source_prefixes: tuple[str, ...]
    forbidden_prefixes: tuple[str, ...]
    excluded_importers: tuple[str, ...] = ()

    def violations(self, graph: PythonImportGraph) -> list[str]:
        return [
            edge.describe()
            for edge in graph.edges
            if _matches_any(edge.importer, self.source_prefixes)
            and _matches_any(edge.imported, self.forbidden_prefixes)
            and not _matches_any(edge.importer, self.excluded_importers)
        ]


@dataclass(frozen=True, slots=True)
class PythonImportGraph:
    package: str
    edges: tuple[ImportEdge, ...]

    @classmethod
    def build(cls, root: Path, *, package: str) -> PythonImportGraph:
        root = root.resolve()
        modules = tuple(
            _module_source(root, path, package=package)
            for path in sorted(root.rglob("*.py"))
        )
        known_modules = _known_modules(modules, package=package)
        edges = [
            ImportEdge(
                importer=source.module,
                imported=imported,
                path=PurePosixPath(source.path.relative_to(root).as_posix()),
                line=line,
            )
            for source in modules
            for imported, line in _imports_from_source(
                source,
                known_modules=known_modules,
            )
        ]
        return cls(
            package=package,
            edges=tuple(
                sorted(
                    edges,
                    key=lambda edge: (
                        str(edge.path),
                        edge.line,
                        edge.imported,
                    ),
                )
            ),
        )

    def dependencies_for_paths(
        self,
        paths: tuple[PurePosixPath, ...],
        *,
        recursive: bool,
    ) -> tuple[ImportEdge, ...]:
        return tuple(
            edge
            for edge in self.edges
            if any(
                edge.path == path
                or (recursive and _is_relative_to(edge.path, path))
                for path in paths
            )
        )


@dataclass(frozen=True, slots=True)
class _ModuleSource:
    module: str
    package: str
    path: Path


def _module_source(root: Path, path: Path, *, package: str) -> _ModuleSource:
    relative = path.relative_to(root).with_suffix("")
    parts = list(relative.parts)
    is_package = parts[-1] == "__init__"
    if is_package:
        parts.pop()
    module = ".".join((package, *parts)) if parts else package
    parent_package = module if is_package else module.rpartition(".")[0]
    return _ModuleSource(module=module, package=parent_package, path=path)


def _known_modules(
    sources: tuple[_ModuleSource, ...],
    *,
    package: str,
) -> frozenset[str]:
    modules = {package}
    for source in sources:
        parts = source.module.split(".")
        for index in range(len(package.split(".")), len(parts) + 1):
            modules.add(".".join(parts[:index]))
    return frozenset(modules)


def _imports_from_source(
    source: _ModuleSource,
    *,
    known_modules: frozenset[str],
) -> tuple[tuple[str, int], ...]:
    tree = ast.parse(
        source.path.read_text(encoding="utf-8"),
        filename=str(source.path),
    )
    imports: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend((alias.name, node.lineno) for alias in node.names)
            continue
        if not isinstance(node, ast.ImportFrom):
            continue
        base = _resolve_import_from_base(source, node)
        if not base:
            continue
        targets = _import_from_targets(base, node, known_modules=known_modules)
        imports.extend((target, node.lineno) for target in targets)
    return tuple(imports)


def _resolve_import_from_base(
    source: _ModuleSource,
    node: ast.ImportFrom,
) -> str | None:
    if node.level == 0:
        return node.module

    package_parts = source.package.split(".")
    parents_to_remove = node.level - 1
    if parents_to_remove >= len(package_parts):
        return None
    anchor = package_parts[: len(package_parts) - parents_to_remove]
    if node.module:
        anchor.extend(node.module.split("."))
    return ".".join(anchor)


def _import_from_targets(
    base: str,
    node: ast.ImportFrom,
    *,
    known_modules: frozenset[str],
) -> tuple[str, ...]:
    targets: list[str] = []
    for alias in node.names:
        candidate = f"{base}.{alias.name}"
        targets.append(candidate if candidate in known_modules else base)
    return tuple(dict.fromkeys(targets))


def _matches_any(module: str, prefixes: tuple[str, ...]) -> bool:
    return any(module == prefix or module.startswith(f"{prefix}.") for prefix in prefixes)


def _is_relative_to(path: PurePosixPath, parent: PurePosixPath) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


__all__ = [
    "DependencyContract",
    "ImportEdge",
    "PythonImportGraph",
]
