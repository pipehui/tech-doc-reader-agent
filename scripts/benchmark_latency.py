"""
端到端延迟 & 首 token 时间（TTFT）测试脚本
================================================

用法：
    # 1. 先启动你的 FastAPI 服务
    # 2. 准备一个 queries.txt，每行一条测试问题
    # 3. 运行：
    python scripts/benchmark_latency.py --queries bench_queries.txt --runs 3

输出：
    - bench_results.jsonl  每次运行的原始数据
    - 控制台打印统计摘要：TTFT / 端到端延迟的 avg / p50 / p95
"""

import argparse
import asyncio
import json
import statistics
import time
import uuid
from collections.abc import AsyncIterator
from collections import defaultdict, deque
from pathlib import Path
from typing import Optional

import httpx


# ============================================================
# ⚠️ 核对以下两项，要和你项目里的实际实现一致
# ============================================================
API_URL = "http://127.0.0.1:8000/chat"

def build_payload(query: str, session_id: str) -> dict:
    """
    根据你的 FastAPI /chat 接口的 Pydantic schema 来构造 payload。
    打开你的路由代码（app/api 下）看一下 POST /chat 的入参定义，
    把下面的字段名调整对应即可。
    """
    return {
        "message": query,
        "session_id": session_id,
    }


# ============================================================
async def iter_sse_events(
    response: httpx.Response,
) -> AsyncIterator[tuple[str, dict]]:
    """解析标准 SSE 响应，产出 (event_name, payload)。"""
    event_name = "message"
    data_lines: list[str] = []

    async for line in response.aiter_lines():
        if line.startswith(":"):
            # SSE keepalive/comment，例如 ": ping"
            continue

        if not line:
            if not data_lines:
                event_name = "message"
                continue

            data_str = "\n".join(data_lines).strip()
            data_lines.clear()

            if not data_str:
                event_name = "message"
                continue

            try:
                payload = json.loads(data_str)
            except json.JSONDecodeError:
                event_name = "message"
                continue

            yield event_name, payload
            event_name = "message"
            continue

        if line.startswith("event:"):
            event_name = line[len("event:"):].strip() or "message"
        elif line.startswith("data:"):
            data_lines.append(line[len("data:"):].strip())

    if data_lines:
        data_str = "\n".join(data_lines).strip()
        if data_str:
            try:
                payload = json.loads(data_str)
            except json.JSONDecodeError:
                return
            yield event_name, payload


def load_queries(query_file: Path) -> list[dict]:
    """
    读取 bench_queries.txt。

    支持格式：
        用户问题 ||| expected_plan
    如果没有 |||，整行都作为 query。
    """
    queries: list[dict] = []

    for raw_line in query_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        if "|||" in line:
            query, expected_plan = line.split("|||", 1)
            queries.append(
                {
                    "query": query.strip(),
                    "expected_plan": expected_plan.strip(),
                }
            )
        else:
            queries.append(
                {
                    "query": line,
                    "expected_plan": None,
                }
            )

    return queries


async def measure_one(
    client: httpx.AsyncClient,
    query: str,
    session_id: str,
    api_url: str,
    expected_plan: str | None = None,
    timeout_s: float = 120.0,
) -> dict:
    """对单个 query 发起一次请求，返回延迟指标"""
    payload = build_payload(query, session_id)

    t_start = time.perf_counter()
    t_first_token: Optional[float] = None
    t_done: Optional[float] = None
    token_count = 0
    tool_calls = 0
    tool_results = 0
    interrupted = False
    error_msg: Optional[str] = None
    last_event_type: Optional[str] = None
    last_agent: Optional[str] = None
    event_count = 0
    recent_events: deque[dict] = deque(maxlen=10)

    try:
        async with asyncio.timeout(timeout_s):
            async with client.stream(
                "POST", api_url, json=payload, timeout=timeout_s
            ) as resp:
                if resp.status_code != 200:
                    return {
                        "query": query,
                        "expected_plan": expected_plan,
                        "session_id": session_id,
                        "error": f"HTTP {resp.status_code}",
                        "last_event_type": last_event_type,
                        "last_agent": last_agent,
                        "event_count": event_count,
                        "recent_events": list(recent_events),
                    }
                async for event_type, event in iter_sse_events(resp):
                    now = time.perf_counter()
                    event_count += 1
                    event_agent = event.get("agent")
                    last_event_type = event_type
                    if event_agent:
                        last_agent = event_agent
                    recent_events.append(
                        {
                            "event": event_type,
                            "agent": event_agent,
                            "payload": compact_payload(event),
                        }
                    )

                    if event_type == "token":
                        if t_first_token is None:
                            t_first_token = now
                        token_count += 1
                    elif event_type == "tool_call":
                        tool_calls += 1
                    elif event_type == "tool_result":
                        tool_results += 1
                    elif event_type == "interrupt_required":
                        interrupted = True
                        break  # 遇到人工审批就跳过这一条
                    elif event_type == "done":
                        t_done = now
                        break
                    elif event_type == "error":
                        error_msg = event.get("message", "unknown error")
                        break
    except TimeoutError:
        return {
            "query": query,
            "expected_plan": expected_plan,
            "session_id": session_id,
            "error": f"TimeoutError: exceeded {timeout_s:.0f}s overall wall time",
            "last_event_type": last_event_type,
            "last_agent": last_agent,
            "event_count": event_count,
            "recent_events": list(recent_events),
        }
    except Exception as e:
        return {
            "query": query,
            "expected_plan": expected_plan,
            "session_id": session_id,
            "error": f"{type(e).__name__}: {e}",
            "last_event_type": last_event_type,
            "last_agent": last_agent,
            "event_count": event_count,
            "recent_events": list(recent_events),
        }

    t_end = t_done if t_done is not None else time.perf_counter()

    return {
        "query": query,
        "expected_plan": expected_plan,
        "session_id": session_id,
        "ttft_s": (t_first_token - t_start) if t_first_token else None,
        "e2e_s": t_end - t_start,
        "tokens": token_count,
        "tool_calls": tool_calls,
        "tool_results": tool_results,
        "tool_events": tool_calls + tool_results,
        "interrupted": interrupted,
        "error": error_msg,
        "last_event_type": last_event_type,
        "last_agent": last_agent,
        "event_count": event_count,
        "recent_events": list(recent_events),
    }


def pct(data: list[float], p: int) -> float:
    if not data:
        return float("nan")
    s = sorted(data)
    k = int(len(s) * p / 100)
    return s[min(k, len(s) - 1)]


def compact_payload(payload: dict, max_text: int = 120) -> dict:
    """压缩事件 payload，方便写入诊断结果。"""
    compact = {
        "agent": payload.get("agent"),
        "tool": payload.get("tool"),
        "session_id": payload.get("session_id"),
    }

    text = payload.get("text")
    if isinstance(text, str) and text:
        compact["text"] = text[:max_text]

    content = payload.get("content")
    if isinstance(content, str) and content:
        compact["content"] = content[:max_text]

    label = payload.get("label")
    if isinstance(label, str) and label:
        compact["label"] = label[:max_text]

    return {k: v for k, v in compact.items() if v not in (None, "")}


def summarize_bucket(rows: list[dict]) -> dict:
    valid = [
        r for r in rows
        if r.get("e2e_s") is not None
        and not r.get("interrupted")
        and not r.get("error")
    ]
    interrupted = [r for r in rows if r.get("interrupted")]
    errored = [r for r in rows if r.get("error")]

    summary = {
        "total": len(rows),
        "valid": len(valid),
        "interrupted": len(interrupted),
        "errored": len(errored),
        "ttft_avg": None,
        "ttft_p50": None,
        "ttft_p95": None,
        "e2e_avg": None,
        "e2e_p50": None,
        "e2e_p95": None,
        "tool_avg": None,
        "tool_max": None,
    }

    if not valid:
        return summary

    ttfts = [r["ttft_s"] for r in valid if r.get("ttft_s") is not None]
    e2es = [r["e2e_s"] for r in valid]
    tools = [r.get("tool_events", r["tool_calls"]) for r in valid]

    if ttfts:
        summary["ttft_avg"] = statistics.mean(ttfts)
        summary["ttft_p50"] = pct(ttfts, 50)
        summary["ttft_p95"] = pct(ttfts, 95)

    summary["e2e_avg"] = statistics.mean(e2es)
    summary["e2e_p50"] = pct(e2es, 50)
    summary["e2e_p95"] = pct(e2es, 95)
    summary["tool_avg"] = statistics.mean(tools)
    summary["tool_max"] = max(tools)
    return summary


def print_bucket_summary(title: str, grouped_rows: dict[str, list[dict]], sort_key: str = "e2e_avg") -> None:
    print(f"\n=== {title} ===")
    table = []
    for name, rows in grouped_rows.items():
        stats = summarize_bucket(rows)
        table.append((name, stats))

    def sort_value(item: tuple[str, dict]) -> tuple[float, str]:
        name, stats = item
        value = stats.get(sort_key)
        if value is None:
            return (-1.0, name)
        return (value, name)

    for name, stats in sorted(table, key=sort_value, reverse=True):
        if stats["valid"] == 0:
            print(
                f"{name}: total={stats['total']}  valid=0  "
                f"interrupted={stats['interrupted']}  errored={stats['errored']}"
            )
            continue

        ttft_text = (
            f"TTFT avg={stats['ttft_avg']:.2f}s  "
            f"p50={stats['ttft_p50']:.2f}s  "
            f"p95={stats['ttft_p95']:.2f}s"
            if stats["ttft_avg"] is not None
            else "TTFT avg=N/A"
        )
        print(
            f"{name}: total={stats['total']}  valid={stats['valid']}  "
            f"interrupted={stats['interrupted']}  errored={stats['errored']}  "
            f"{ttft_text}  "
            f"E2E avg={stats['e2e_avg']:.2f}s  p50={stats['e2e_p50']:.2f}s  p95={stats['e2e_p95']:.2f}s  "
            f"tools avg={stats['tool_avg']:.1f}  max={stats['tool_max']}"
        )


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--queries", type=Path, required=True)
    parser.add_argument("--api-url", type=str, default=API_URL, help="要压测的 /chat 接口地址")
    parser.add_argument("--runs", type=int, default=1, help="每条 query 重复次数")
    parser.add_argument("--warmup", type=int, default=1, help="预热请求次数（不计入统计）")
    parser.add_argument("--timeout", type=float, default=120.0, help="单次请求的总墙钟超时（秒）")
    parser.add_argument("--output", type=Path, default=Path("bench_results.jsonl"))
    args = parser.parse_args()

    queries = load_queries(args.queries)
    print(f"Loaded {len(queries)} queries, {args.runs} run(s) each.")

    results = []

    async with httpx.AsyncClient() as client:
        # ---------- warmup ----------
        if args.warmup > 0 and queries:
            print(f"Warming up ({args.warmup} request(s))...")
            for i in range(args.warmup):
                await measure_one(
                    client,
                    queries[0]["query"],
                    f"warmup_{uuid.uuid4().hex[:8]}",
                    api_url=args.api_url,
                    expected_plan=queries[0]["expected_plan"],
                    timeout_s=args.timeout,
                )

        # ---------- 正式测试 ----------
        total = len(queries) * args.runs
        idx = 0
        for item in queries:
            query = item["query"]
            expected_plan = item["expected_plan"]
            for r in range(args.runs):
                idx += 1
                session_id = f"bench_{uuid.uuid4().hex[:12]}"  # 每次新 session，避免历史影响
                plan_note = f" | expected={expected_plan}" if expected_plan else ""
                print(f"[{idx}/{total}] {query[:50]}{plan_note}")
                result = await measure_one(
                    client,
                    query,
                    session_id,
                    api_url=args.api_url,
                    expected_plan=expected_plan,
                    timeout_s=args.timeout,
                )
                results.append(result)
                ttft = result.get("ttft_s")
                e2e = result.get("e2e_s")
                if result.get("error"):
                    print(f"   ERROR: {result['error']}")
                    if result.get("last_event_type") or result.get("last_agent"):
                        print(
                            f"   last_event={result.get('last_event_type')}  "
                            f"last_agent={result.get('last_agent')}  "
                            f"events={result.get('event_count', 0)}"
                        )
                    if result.get("recent_events"):
                        for item in result["recent_events"][-3:]:
                            print(f"   recent: {json.dumps(item, ensure_ascii=False)}")
                elif result.get("interrupted"):
                    print(f"   (interrupted, skipped)")
                    if result.get("last_event_type") or result.get("last_agent"):
                        print(
                            f"   last_event={result.get('last_event_type')}  "
                            f"last_agent={result.get('last_agent')}  "
                            f"events={result.get('event_count', 0)}"
                        )
                    if result.get("recent_events"):
                        for item in result["recent_events"][-3:]:
                            print(f"   recent: {json.dumps(item, ensure_ascii=False)}")
                else:
                    ttft_text = f"{ttft:.2f}s" if ttft is not None else "N/A"
                    print(
                        f"   TTFT={ttft_text}  "
                        f"E2E={e2e:.2f}s  "
                        f"tokens={result['tokens']}  "
                        f"tools={result.get('tool_events', result['tool_calls'])} "
                        f"(calls={result['tool_calls']}, results={result.get('tool_results', 0)})"
                    )

    # ---------- 写入原始结果 ----------
    with args.output.open("w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"\nRaw results saved to {args.output}")

    # ---------- 统计 ----------
    valid = [
        r for r in results
        if r.get("e2e_s") is not None
        and not r.get("interrupted")
        and not r.get("error")
    ]
    if not valid:
        print("No valid results.")
        return

    ttfts = [r["ttft_s"] for r in valid if r.get("ttft_s") is not None]
    e2es = [r["e2e_s"] for r in valid]

    print("\n=== Summary ===")
    print(f"Valid runs: {len(valid)} / {len(results)}")
    if ttfts:
        print(
            f"TTFT  avg={statistics.mean(ttfts):.2f}s  "
            f"p50={pct(ttfts, 50):.2f}s  p95={pct(ttfts, 95):.2f}s"
        )
    print(
        f"E2E   avg={statistics.mean(e2es):.2f}s  "
        f"p50={pct(e2es, 50):.2f}s  p95={pct(e2es, 95):.2f}s"
    )

    # 工具调用分布，顺便看看
    tool_counts = [r["tool_calls"] for r in valid]
    print(f"Tool calls per query: avg={statistics.mean(tool_counts):.1f}  max={max(tool_counts)}")

    # ---------- 分组统计 ----------
    by_plan: dict[str, list[dict]] = defaultdict(list)
    by_query: dict[str, list[dict]] = defaultdict(list)

    for row in results:
        by_plan[row.get("expected_plan") or "unlabeled"].append(row)
        by_query[row["query"]].append(row)

    print_bucket_summary("By Expected Plan", dict(by_plan))
    print_bucket_summary("By Query", dict(by_query))


if __name__ == "__main__":
    asyncio.run(main())
