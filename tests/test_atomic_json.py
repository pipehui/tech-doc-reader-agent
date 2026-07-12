import json

import pytest

from tech_doc_agent.app.core.errors import DependencyUnavailable, ValidationError
from tech_doc_agent.app.infrastructure.persistence import atomic_json
from tech_doc_agent.app.infrastructure.persistence.atomic_json import read_json, write_json_atomic


def test_write_json_atomic_creates_parent_and_replaces_existing_file(tmp_path):
    path = tmp_path / "nested" / "state.json"
    write_json_atomic(path, {"version": 1})
    write_json_atomic(path, {"version": 2, "message": "已更新"})

    assert read_json(path) == {"version": 2, "message": "已更新"}


def test_write_json_atomic_preserves_existing_file_when_serialization_fails(tmp_path):
    path = tmp_path / "state.json"
    write_json_atomic(path, {"version": 1})

    with pytest.raises(ValidationError) as exc_info:
        write_json_atomic(path, {"invalid": object()})

    assert exc_info.value.code == "validation_error"
    assert exc_info.value.dependency == "file_repository"
    assert exc_info.value.cause_type == "TypeError"
    assert read_json(path) == {"version": 1}
    assert list(tmp_path.glob(".state.json.*.tmp")) == []


def test_write_json_atomic_preserves_existing_file_when_replace_fails(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    write_json_atomic(path, {"version": 1})

    def fail_replace(source, destination):
        raise OSError("replace failed")

    monkeypatch.setattr(atomic_json.os, "replace", fail_replace)

    with pytest.raises(DependencyUnavailable) as exc_info:
        write_json_atomic(path, {"version": 2})

    assert exc_info.value.dependency == "file_repository"
    assert exc_info.value.cause_type == "OSError"
    assert "replace failed" not in str(exc_info.value)
    assert json.loads(path.read_text(encoding="utf-8")) == {"version": 1}
    assert list(tmp_path.glob(".state.json.*.tmp")) == []


def test_read_json_maps_corrupt_content_without_returning_raw_parser_details(tmp_path):
    path = tmp_path / "corrupt.json"
    path.write_text('{"token": "private",', encoding="utf-8")

    with pytest.raises(ValidationError) as exc_info:
        read_json(path)

    assert exc_info.value.dependency == "file_repository"
    assert exc_info.value.cause_type == "JSONDecodeError"
    assert "private" not in str(exc_info.value)
