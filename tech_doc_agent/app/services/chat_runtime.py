import asyncio
from contextlib import suppress
from tech_doc_agent.app.graph import build_multi_agentic_graph
from tech_doc_agent.app.core.langfuse_tracing import (
    build_langfuse_trace,
    flush_langfuse,
    langfuse_metadata,
    shutdown_langfuse,
)
from tech_doc_agent.app.core.observability import get_trace_context, log_event, timed_node
from tech_doc_agent.app.core.settings import get_settings
from tech_doc_agent.app.services.resources import AppResources, reset_app_resources, set_app_resources
from langchain_core.messages import ToolMessage
from langgraph.checkpoint.redis import RedisSaver
from langgraph.types import StateSnapshot
from redis.exceptions import BusyLoadingError
from time import perf_counter, sleep
from typing import Any


_STREAM_DONE = object()


def _elapsed_ms(start: float) -> float:
    return round((perf_counter() - start) * 1000, 2)


def _error_message(exc: Exception) -> str:
    return str(exc) or type(exc).__name__


def _next_or_done(iterator):
    try:
        return next(iterator)
    except StopIteration:
        return _STREAM_DONE


def _is_retryable_redis_startup_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return isinstance(exc, BusyLoadingError) or "redis is loading" in message or "loading the dataset" in message


async def _aiter_sync_iterator(parts):
    iterator = iter(parts)

    try:
        while True:
            part = await asyncio.to_thread(_next_or_done, iterator)
            if part is _STREAM_DONE:
                return
            yield part
    finally:
        close = getattr(iterator, "close", None)
        if callable(close):
            with suppress(Exception):
                await asyncio.to_thread(close)


class ChatRuntime:
    def __init__(self) -> None:
        self.settings = get_settings()
        self._checkpointer_cm: Any | None = None
        self.checkpointer: Any | None = None
        self.graph: Any | None = None
        self.resources: Any | None = None

    def __enter__(self):
        try:
            self.resources = AppResources.create(self.settings)
            set_app_resources(self.resources)
            self._setup_checkpointer_with_retry()
            self.graph = build_multi_agentic_graph(self.checkpointer)
            return self
        except Exception:
            self._close_checkpointer()
            reset_app_resources()
            raise

    def __exit__(self, exc_type, exc, tb):
        shutdown_langfuse(self.settings)

        try:
            self._close_checkpointer(exc_type, exc, tb)
        finally:
            reset_app_resources()

    def _setup_checkpointer_with_retry(self) -> None:
        max_attempts = max(1, int(self.settings.REDIS_SETUP_MAX_ATTEMPTS))
        retry_seconds = max(0.0, float(self.settings.REDIS_SETUP_RETRY_SECONDS))

        for attempt in range(1, max_attempts + 1):
            self._checkpointer_cm = RedisSaver.from_conn_string(self.settings.REDIS_URL)
            try:
                self.checkpointer = self._checkpointer_cm.__enter__()
                self.checkpointer.setup()
                if attempt > 1:
                    log_event("redis.checkpointer.setup.ready", attempt=attempt)
                return
            except Exception as exc:
                self._close_checkpointer()
                if attempt >= max_attempts or not _is_retryable_redis_startup_error(exc):
                    raise
                log_event(
                    "redis.checkpointer.setup.retry",
                    attempt=attempt,
                    max_attempts=max_attempts,
                    retry_seconds=retry_seconds,
                    error_type=type(exc).__name__,
                    error=str(exc),
                )
                sleep(retry_seconds)

    def _close_checkpointer(self, exc_type=None, exc=None, tb=None) -> None:
        if self._checkpointer_cm is not None:
            self._checkpointer_cm.__exit__(exc_type, exc, tb)
        self._checkpointer_cm = None
        self.checkpointer = None

    def _require_graph(self) -> Any:
        if self.graph is None:
            raise RuntimeError("ChatRuntime graph is not initialized.")
        return self.graph

    def build_config(
        self,
        session_id: str,
        operation: str = "state",
        with_callbacks: bool = False,
    ) -> dict:
        context = get_trace_context()
        trace_id = context.get("trace_id")
        langfuse_trace = (
            build_langfuse_trace(self.settings, trace_id)
            if with_callbacks and isinstance(trace_id, str)
            else None
        )
        metadata = {
            "session_id": session_id,
            **langfuse_metadata(
                session_id=session_id,
                operation=operation,
                external_trace_id=trace_id if isinstance(trace_id, str) else None,
                langfuse_trace=langfuse_trace,
            ),
        }

        config = {
            "configurable": {
                "thread_id": session_id,
            },
            "metadata": metadata,
            "run_name": f"tech_doc_agent.{operation}",
            "recursion_limit": self.settings.LANGGRAPH_RECURSION_LIMIT,
        }

        if langfuse_trace is not None:
            config["callbacks"] = [langfuse_trace.callback]

        return config
    
    def preload_printed_message_ids(self, session_id: str) -> set[str]:
        printed_message_ids = set()

        snapshot = self.get_snapshot(session_id)
        state_value = getattr(snapshot, "values", None)

        if state_value and "messages" in state_value:
            for message in state_value["messages"]:
                if getattr(message, "id", None):
                    printed_message_ids.add(message.id)

        return printed_message_ids
    
    def stream_user_message(self, session_id: str, user_input: str):
        start = perf_counter()
        log_event(
            "chat.request.started",
            session_id=session_id,
            message_length=len(user_input),
        )

        try:
            with timed_node("graph.stream", phase="chat"):
                graph = self._require_graph()
                config = self.build_config(
                    session_id,
                    operation="chat",
                    with_callbacks=True,
                )
                parts = graph.stream(
                    {"messages": [("user", user_input)]},
                    config,
                    stream_mode=["messages", "updates"],
                    version="v2",
                )
                for part in parts:
                    yield part
        except Exception as exc:
            log_event(
                "chat.request.error",
                session_id=session_id,
                elapsed_ms=_elapsed_ms(start),
                error_type=type(exc).__name__,
                error=_error_message(exc),
            )
            raise

        pending_interrupt = self.has_pending_interrupt(session_id)
        log_event(
            "chat.request.interrupted" if pending_interrupt else "chat.request.finished",
            session_id=session_id,
            elapsed_ms=_elapsed_ms(start),
            pending_interrupt=pending_interrupt,
        )
        if self.settings.LANGFUSE_FLUSH_ON_REQUEST:
            flush_langfuse(self.settings)

    async def astream_user_message(self, session_id: str, user_input: str):
        start = perf_counter()
        log_event(
            "chat.request.started",
            session_id=session_id,
            message_length=len(user_input),
            async_runtime=True,
        )

        try:
            graph = self._require_graph()
            config = self.build_config(
                session_id,
                operation="chat",
                with_callbacks=True,
            )

            with timed_node("graph.stream.thread", phase="chat"):
                async for part in _aiter_sync_iterator(
                    graph.stream(
                        {"messages": [("user", user_input)]},
                        config,
                        stream_mode=["messages", "updates"],
                        version="v2",
                    )
                ):
                    yield part
        except Exception as exc:
            log_event(
                "chat.request.error",
                session_id=session_id,
                elapsed_ms=_elapsed_ms(start),
                async_runtime=True,
                error_type=type(exc).__name__,
                error=_error_message(exc),
            )
            raise

        pending_interrupt = await self.ahas_pending_interrupt(session_id)
        log_event(
            "chat.request.interrupted" if pending_interrupt else "chat.request.finished",
            session_id=session_id,
            elapsed_ms=_elapsed_ms(start),
            pending_interrupt=pending_interrupt,
            async_runtime=True,
        )
        if self.settings.LANGFUSE_FLUSH_ON_REQUEST:
            await asyncio.to_thread(flush_langfuse, self.settings)

    def has_pending_interrupt(self, session_id: str) -> bool:
        snapshot = self.get_snapshot(session_id)
        return bool(snapshot.next)

    async def ahas_pending_interrupt(self, session_id: str) -> bool:
        return await asyncio.to_thread(self.has_pending_interrupt, session_id)

    def stream_approval(self, session_id: str, approved: bool, feedback: str = ""):
        start = perf_counter()
        log_event("chat.approval.started", session_id=session_id, approved=approved)

        try:
            snapshot = self.get_snapshot(session_id)

            if not snapshot.next:
                log_event(
                    "chat.approval.no_pending_interrupt",
                    session_id=session_id,
                    elapsed_ms=_elapsed_ms(start),
                    approved=approved,
                )
                return

            config = self.build_config(
                session_id,
                operation="approval",
                with_callbacks=True,
            )

            if approved:
                graph = self._require_graph()
                parts = graph.stream(None, config, stream_mode=["messages", "updates"], version="v2")
            else:
                graph = self._require_graph()
                tool_call_id = snapshot.values["messages"][-1].tool_calls[0]["id"]
                feedback = feedback or "用户未提供原因"
                parts = graph.stream(
                    {
                        "messages": [
                            ToolMessage(
                                tool_call_id=tool_call_id,
                                content=f"用户拒绝了此操作。原因：'{feedback}'。请根据用户的反馈继续协助。",
                            )
                        ]
                    },
                    config,
                    stream_mode=["messages", "updates"],
                    version="v2",
                )

            with timed_node("graph.stream", phase="approval", approved=approved):
                for part in parts:
                    yield part
        except Exception as exc:
            log_event(
                "chat.approval.error",
                session_id=session_id,
                elapsed_ms=_elapsed_ms(start),
                approved=approved,
                error_type=type(exc).__name__,
                error=_error_message(exc),
            )
            raise

        pending_interrupt = self.has_pending_interrupt(session_id)
        log_event(
            "chat.approval.interrupted" if pending_interrupt else "chat.approval.finished",
            session_id=session_id,
            elapsed_ms=_elapsed_ms(start),
            approved=approved,
            pending_interrupt=pending_interrupt,
        )
        if self.settings.LANGFUSE_FLUSH_ON_REQUEST:
            flush_langfuse(self.settings)

    async def astream_approval(self, session_id: str, approved: bool, feedback: str = ""):
        start = perf_counter()
        log_event(
            "chat.approval.started",
            session_id=session_id,
            approved=approved,
            async_runtime=True,
        )

        try:
            snapshot = await self.aget_snapshot(session_id)

            if not snapshot.next:
                log_event(
                    "chat.approval.no_pending_interrupt",
                    session_id=session_id,
                    elapsed_ms=_elapsed_ms(start),
                    approved=approved,
                    async_runtime=True,
                )
                return

            config = self.build_config(
                session_id,
                operation="approval",
                with_callbacks=True,
            )
            graph = self._require_graph()

            if approved:
                graph_input = None
            else:
                tool_call_id = snapshot.values["messages"][-1].tool_calls[0]["id"]
                feedback = feedback or "用户未提供原因"
                graph_input = {
                    "messages": [
                        ToolMessage(
                            tool_call_id=tool_call_id,
                            content=f"用户拒绝了此操作。原因：'{feedback}'。请根据用户的反馈继续协助。",
                        )
                    ]
                }

            with timed_node("graph.stream.thread", phase="approval", approved=approved):
                async for part in _aiter_sync_iterator(
                    graph.stream(
                        graph_input,
                        config,
                        stream_mode=["messages", "updates"],
                        version="v2",
                    )
                ):
                    yield part
        except Exception as exc:
            log_event(
                "chat.approval.error",
                session_id=session_id,
                elapsed_ms=_elapsed_ms(start),
                approved=approved,
                async_runtime=True,
                error_type=type(exc).__name__,
                error=_error_message(exc),
            )
            raise

        pending_interrupt = await self.ahas_pending_interrupt(session_id)
        log_event(
            "chat.approval.interrupted" if pending_interrupt else "chat.approval.finished",
            session_id=session_id,
            elapsed_ms=_elapsed_ms(start),
            approved=approved,
            pending_interrupt=pending_interrupt,
            async_runtime=True,
        )
        if self.settings.LANGFUSE_FLUSH_ON_REQUEST:
            await asyncio.to_thread(flush_langfuse, self.settings)

    def get_snapshot(self, session_id: str) -> StateSnapshot:
        return self._require_graph().get_state(self.build_config(session_id))

    async def aget_snapshot(self, session_id: str) -> StateSnapshot:
        return await asyncio.to_thread(self.get_snapshot, session_id)
    
    def _extract_text_content(self, content) -> str:
        if isinstance(content, str):
            return content

        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    if item.get("type") == "text":
                        parts.append(item.get("text", ""))
                    elif "text" in item:
                        parts.append(item.get("text", ""))
            return "".join(parts)

        return ""

    def _serialize_message(self, message) -> dict:
        raw_type = getattr(message, "type", "unknown")
        role_map = {
            "human": "user",
            "ai": "assistant",
            "tool": "tool",
            "system": "system",
        }

        return {
            "id": getattr(message, "id", None),
            "role": role_map.get(raw_type, raw_type),
            "raw_type": raw_type,
            "content": self._extract_text_content(getattr(message, "content", "")),
            "name": getattr(message, "name", None),
            "tool_call_id": getattr(message, "tool_call_id", None),
            "tool_calls": getattr(message, "tool_calls", []) or [],
        }
    
    def get_history(self, session_id: str) -> dict:
        snapshot = self.get_snapshot(session_id)
        state_values = getattr(snapshot, "values", None)

        if not isinstance(state_values, dict):
            state_values = {}

        messages = state_values.get("messages", [])

        return {
            "session_id": session_id,
            "learning_target": state_values.get("learning_target"),
            "pending_interrupt": bool(snapshot.next),
            "message_count": len(messages),
            "messages": [self._serialize_message(message) for message in messages],
        }
    
    def _to_history_view_item(self, message) -> dict | None:
        raw_type = getattr(message, "type", "unknown")
        content = self._extract_text_content(getattr(message, "content", ""))

        if raw_type == "human":
            return {
                "id": getattr(message, "id", None),
                "role": "user",
                "kind": "message",
                "content": content,
            }

        if raw_type == "ai":
            if not content.strip():
                return None
            return {
                "id": getattr(message, "id", None),
                "role": "assistant",
                "kind": "message",
                "content": content,
                "name": getattr(message, "name", None),
            }

        if raw_type == "tool":
            return {
                "id": getattr(message, "id", None),
                "role": "tool",
                "kind": "tool_result",
                "content": content,
                "tool_call_id": getattr(message, "tool_call_id", None),
                "name": getattr(message, "name", None),
            }

        return None
    
    def get_history_view(
        self,
        session_id: str,
        include_tools: bool = False,
    ) -> dict:
        snapshot = self.get_snapshot(session_id)
        state_values = getattr(snapshot, "values", None)

        if not isinstance(state_values, dict):
            state_values = {}

        raw_messages = state_values.get("messages", [])
        items = []

        for message in raw_messages:
            item = self._to_history_view_item(message)
            if item is None:
                continue

            if item["role"] == "tool" and not include_tools:
                continue

            items.append(item)

        return {
            "session_id": session_id,
            "learning_target": state_values.get("learning_target"),
            "pending_interrupt": bool(snapshot.next),
            "message_count": len(items),
            "messages": items,
        }

    def get_session_state(self, session_id: str) -> dict:
        snapshot = self.get_snapshot(session_id)
        state_values = getattr(snapshot, "values", None)

        if not isinstance(state_values, dict):
            state_values = {}

        messages = state_values.get("messages", [])
        learning_target = state_values.get("learning_target")

        exists = bool(messages) or bool(learning_target) or bool(snapshot.next)

        dialog_stack = state_values.get("dialog_state", [])
        current_agent = dialog_stack[-1] if dialog_stack else "primary"

        workflow_plan = state_values.get("workflow_plan", [])
        plan_index = state_values.get("plan_index", 0)

        return {
            "session_id": session_id,
            "exists": exists,
            "pending_interrupt": bool(snapshot.next),
            "learning_target": learning_target,
            "message_count": len(messages),
            "current_agent": current_agent,
            "workflow_plan": workflow_plan,
            "plan_index": plan_index,
        }

    async def aget_session_state(self, session_id: str) -> dict:
        return await asyncio.to_thread(self.get_session_state, session_id)
