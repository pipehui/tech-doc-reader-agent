from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse
from redis import Redis
from redis.exceptions import RedisError

from tech_doc_agent.app.core.observability import log_event


router = APIRouter()


def _check(name: str, ok: bool, **details: Any) -> dict[str, Any]:
    return {
        "name": name,
        "ok": ok,
        **{key: value for key, value in details.items() if value is not None},
    }


def _redis_check(redis_url: str) -> dict[str, Any]:
    client = None
    try:
        client = Redis.from_url(
            redis_url,
            socket_connect_timeout=0.5,
            socket_timeout=0.5,
        )
        client.ping()
        return _check("redis", True)
    except RedisError as exc:
        return _check("redis", False, error_type=type(exc).__name__, error=str(exc))
    finally:
        if client is not None:
            client.close()


def readiness_checks(runtime: Any) -> list[dict[str, Any]]:
    if runtime is None:
        return [_check("runtime", False, error="ChatRuntime is not initialized.")]

    resources = getattr(runtime, "resources", None)
    settings = getattr(runtime, "settings", None)
    faiss_store = getattr(resources, "faiss_store", None)
    hybrid_retriever = getattr(resources, "hybrid_retriever", None)
    learning_store = getattr(resources, "learning_store", None)
    memory_store = getattr(resources, "memory_store", None)
    web_search_backend = getattr(resources, "web_search_backend", None)

    checks = [
        _check("runtime", True),
        _check("graph", getattr(runtime, "graph", None) is not None),
        _check("checkpointer", getattr(runtime, "checkpointer", None) is not None),
        _check("resources", resources is not None),
        _check(
            "faiss_store",
            faiss_store is not None and isinstance(getattr(faiss_store, "documents", None), list),
            documents=len(getattr(faiss_store, "documents", []) or []) if faiss_store is not None else None,
            indexed=getattr(faiss_store, "index", None) is not None if faiss_store is not None else None,
        ),
        _check("hybrid_retriever", hybrid_retriever is not None),
        _check(
            "learning_store",
            learning_store is not None and isinstance(getattr(learning_store, "records", None), list),
            records=len(getattr(learning_store, "records", []) or []) if learning_store is not None else None,
        ),
        _check(
            "memory_store",
            memory_store is not None and isinstance(getattr(memory_store, "memories", None), list),
            memories=len(getattr(memory_store, "memories", []) or []) if memory_store is not None else None,
        ),
        _check("web_search_backend", web_search_backend is not None),
    ]

    if settings is None:
        checks.append(_check("redis", False, error="Settings are not initialized."))
    else:
        checks.append(_redis_check(settings.REDIS_URL))

    return checks


@router.get("/health")
def health():
    return {"status": "ok"}


@router.get("/ready")
def ready(request: Request):
    runtime = getattr(request.app.state, "runtime", None)
    checks = readiness_checks(runtime)
    is_ready = all(check["ok"] for check in checks)
    payload = {
        "status": "ready" if is_ready else "not_ready",
        "checks": checks,
    }

    if not is_ready:
        log_event(
            "readiness.failed",
            failed=[check for check in checks if not check["ok"]],
        )

    return JSONResponse(
        payload,
        status_code=status.HTTP_200_OK if is_ready else status.HTTP_503_SERVICE_UNAVAILABLE,
    )
