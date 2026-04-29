from __future__ import annotations

import argparse
import json
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import httpx


DEFAULT_API_URL = "http://127.0.0.1:8000/chat"
DEFAULT_TEMPLATE = (
    "请系统解析并沉淀到文档库：{topic}，"
    "由于我只需要存文档库，所以把任务直接交给parser即可，允许parser多次使用websearch。"
)
DEFAULT_ALLOWED_APPROVAL_TOOLS = {"save_docs"}
SESSION_SAFE_RE = re.compile(r"[^A-Za-z0-9_.:-]+")


def load_topics(path: Path | None, inline_topics: list[str]) -> list[str]:
    topics: list[str] = []

    if path is not None:
        raw = path.read_text(encoding="utf-8").strip()
        if raw.startswith("["):
            loaded = json.loads(raw)
            if not isinstance(loaded, list):
                raise ValueError(f"{path} must contain a JSON array or one topic per line.")
            topics.extend(str(item).strip() for item in loaded)
        else:
            for line in raw.splitlines():
                stripped = line.strip()
                if stripped and not stripped.startswith("#"):
                    topics.append(stripped)

    topics.extend(topic.strip() for topic in inline_topics)
    return [topic for topic in topics if topic]


def build_message(topic: str, template: str) -> str:
    return template.format(topic=topic)


def build_session_id(prefix: str, index: int, topic: str) -> str:
    slug = SESSION_SAFE_RE.sub("-", topic).strip("-_.:")[:48] or uuid.uuid4().hex[:8]
    suffix = uuid.uuid4().hex[:8]
    session_id = f"{prefix}-{index:03d}-{slug}-{suffix}"
    return session_id[:128]


def approve_url_for(api_url: str, approve_url: str | None) -> str:
    if approve_url:
        return approve_url
    if api_url.rstrip("/").endswith("/chat"):
        return f"{api_url.rstrip('/')}/approve"
    return f"{api_url.rstrip('/')}/chat/approve"


def iter_sse_events(response: httpx.Response):
    event_name = "message"
    data_lines: list[str] = []

    for line in response.iter_lines():
        if line.startswith(":"):
            continue

        if not line:
            if data_lines:
                payload = parse_sse_payload(data_lines)
                if payload is not None:
                    yield event_name, payload
            event_name = "message"
            data_lines.clear()
            continue

        if line.startswith("event:"):
            event_name = line[len("event:"):].strip() or "message"
        elif line.startswith("data:"):
            data_lines.append(line[len("data:"):].strip())

    if data_lines:
        payload = parse_sse_payload(data_lines)
        if payload is not None:
            yield event_name, payload


def parse_sse_payload(lines: list[str]) -> dict[str, Any] | None:
    raw = "\n".join(lines).strip()
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else {"value": payload}


def run_topic(
    client: httpx.Client,
    *,
    api_url: str,
    approve_url: str,
    topic: str,
    session_id: str,
    message: str,
    timeout_s: float,
    allowed_approval_tools: set[str],
    max_approval_rounds: int,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "topic": topic,
        "session_id": session_id,
        "status": "unknown",
        "approvals": 0,
        "tool_calls": [],
        "tool_results": 0,
        "error": None,
    }

    payload = {"session_id": session_id, "message": message}
    status, last_tool_name = _stream_until_terminal(
        client,
        url=api_url,
        payload=payload,
        timeout_s=timeout_s,
        result=result,
    )

    while status == "interrupt_required":
        if result["approvals"] >= max_approval_rounds:
            result["status"] = "error"
            result["error"] = f"approval rounds exceeded {max_approval_rounds}"
            return result

        if last_tool_name is not None and last_tool_name not in allowed_approval_tools:
            result["status"] = "error"
            result["error"] = f"refusing to auto-approve tool: {last_tool_name or 'unknown'}"
            return result

        result["approvals"] += 1
        approve_payload = {"session_id": session_id, "approved": True}
        status, last_tool_name = _stream_until_terminal(
            client,
            url=approve_url,
            payload=approve_payload,
            timeout_s=timeout_s,
            result=result,
        )

    result["status"] = status
    return result


def _stream_until_terminal(
    client: httpx.Client,
    *,
    url: str,
    payload: dict[str, Any],
    timeout_s: float,
    result: dict[str, Any],
) -> tuple[str, str | None]:
    last_tool_name: str | None = None
    try:
        with client.stream("POST", url, json=payload, timeout=timeout_s) as response:
            if response.status_code != 200:
                result["error"] = f"HTTP {response.status_code}"
                return "error", last_tool_name

            for event_name, event in iter_sse_events(response):
                if event_name == "tool_call":
                    tool_name = event.get("tool")
                    if isinstance(tool_name, str):
                        last_tool_name = tool_name
                    result["tool_calls"].append(
                        {
                            "tool": tool_name,
                            "args": event.get("args", {}),
                            "tool_call_id": event.get("tool_call_id"),
                        }
                    )
                elif event_name == "tool_result":
                    result["tool_results"] += 1
                elif event_name == "interrupt_required":
                    return "interrupt_required", last_tool_name
                elif event_name == "done":
                    return "done", last_tool_name
                elif event_name == "error":
                    result["error"] = event.get("message") or "unknown SSE error"
                    return "error", last_tool_name
                elif event_name == "no_pending_interrupt":
                    return "no_pending_interrupt", last_tool_name
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        return "error", last_tool_name

    return "stream_ended", last_tool_name


def append_jsonl(path: Path | None, row: dict[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(row, ensure_ascii=False) + "\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Batch seed the document store through parser + save_docs approval.")
    parser.add_argument("--topics-file", type=Path, default=None, help="Topic file. Supports one topic per line or JSON array.")
    parser.add_argument("--topic", action="append", default=[], help="Inline topic. Can be passed multiple times.")
    parser.add_argument("--api-url", default=DEFAULT_API_URL)
    parser.add_argument("--approve-url", default=None)
    parser.add_argument("--template", default=DEFAULT_TEMPLATE)
    parser.add_argument("--session-prefix", default="seed-docs")
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--delay", type=float, default=0.0, help="Seconds to sleep between topics.")
    parser.add_argument("--limit", type=int, default=None, help="Only process the first N topics.")
    parser.add_argument("--max-approval-rounds", type=int, default=3)
    parser.add_argument(
        "--allowed-approval-tool",
        action="append",
        default=sorted(DEFAULT_ALLOWED_APPROVAL_TOOLS),
        help="Tool name allowed for auto approval. Defaults to save_docs.",
    )
    parser.add_argument("--output", type=Path, default=Path("eval_results/doc_seed_runs.jsonl"))
    parser.add_argument("--dry-run", action="store_true", help="Print generated session/message pairs without calling API.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    topics = load_topics(args.topics_file, args.topic)
    if args.limit is not None:
        topics = topics[: args.limit]
    if not topics:
        print("No topics provided. Use --topics-file or --topic.", file=sys.stderr)
        return 2

    approve_url = approve_url_for(args.api_url, args.approve_url)
    allowed_approval_tools = set(args.allowed_approval_tool)

    if args.dry_run:
        for index, topic in enumerate(topics, start=1):
            session_id = build_session_id(args.session_prefix, index, topic)
            print(json.dumps({"session_id": session_id, "message": build_message(topic, args.template)}, ensure_ascii=False))
        return 0

    with httpx.Client() as client:
        for index, topic in enumerate(topics, start=1):
            session_id = build_session_id(args.session_prefix, index, topic)
            message = build_message(topic, args.template)
            print(f"[{index}/{len(topics)}] {topic}")
            started_at = time.perf_counter()
            row = run_topic(
                client,
                api_url=args.api_url,
                approve_url=approve_url,
                topic=topic,
                session_id=session_id,
                message=message,
                timeout_s=args.timeout,
                allowed_approval_tools=allowed_approval_tools,
                max_approval_rounds=args.max_approval_rounds,
            )
            row["elapsed_s"] = time.perf_counter() - started_at
            append_jsonl(args.output, row)
            print(
                "  "
                f"status={row['status']} approvals={row['approvals']} "
                f"tool_calls={len(row['tool_calls'])} tool_results={row['tool_results']} "
                f"elapsed={row['elapsed_s']:.1f}s"
            )
            if row.get("error"):
                print(f"  error={row['error']}")

            if row["status"] != "done":
                return 1

            if args.delay > 0 and index < len(topics):
                time.sleep(args.delay)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
