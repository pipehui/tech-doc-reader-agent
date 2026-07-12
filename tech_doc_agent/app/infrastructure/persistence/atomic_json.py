from __future__ import annotations

import json
import os
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import Any

from tech_doc_agent.app.core.errors import classify_error


def read_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as file:
            return json.load(file)
    except Exception as exc:
        raise classify_error(exc, dependency="file_repository") from exc


def write_json_atomic(path: Path, value: Any) -> None:
    temporary_path: Path | None = None

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as file:
            temporary_path = Path(file.name)
            json.dump(value, file, ensure_ascii=False, indent=2)
            file.flush()
            os.fsync(file.fileno())

        os.replace(temporary_path, path)
        temporary_path = None
    except Exception as exc:
        raise classify_error(exc, dependency="file_repository") from exc
    finally:
        if temporary_path is not None:
            # Cleanup must not replace the typed primary failure with a second OS error.
            with suppress(OSError):
                temporary_path.unlink()
