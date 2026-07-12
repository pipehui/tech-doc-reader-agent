from __future__ import annotations

from typing import Any, TypeGuard


FULL_GIT_COMMIT_PATTERN = r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$"


def is_full_git_commit_sha(value: Any) -> TypeGuard[str]:
    return (
        isinstance(value, str)
        and len(value) in (40, 64)
        and all(character in "0123456789abcdef" for character in value)
    )


__all__ = ["FULL_GIT_COMMIT_PATTERN", "is_full_git_commit_sha"]
