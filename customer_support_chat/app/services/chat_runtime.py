from customer_support_chat.app.graph import build_multi_agentic_graph
from customer_support_chat.app.core.settings import get_settings
from langchain_core.messages import ToolMessage
from langgraph.checkpoint.redis import RedisSaver
from langgraph.types import StateSnapshot

class ChatRuntime:
    def __init__(self) -> None:
        self.settings = get_settings()
        self._checkpointer_cm = None
        self.checkpointer = None
        self.graph = None

    def __enter__(self):
        self._checkpointer_cm = RedisSaver.from_conn_string(self.settings.REDIS_URL)
        self.checkpointer = self._checkpointer_cm.__enter__()
        self.checkpointer.setup()
        self.graph = build_multi_agentic_graph(self.checkpointer)
        return self

    def __exit__(self, exc_type, exc, tb):
        if self._checkpointer_cm is not None:
            self._checkpointer_cm.__exit__(exc_type, exc, tb)

    def build_config(self, session_id: str) -> dict:
        return {
             "configurable": {
                "thread_id": session_id,
            }
        }
    
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
        # events = self.graph.stream(
        #     {"messages": [("user", user_input)]}, self.build_config(session_id), stream_mode="messages", version="v2",
        # )
        # for event in events:
        #     messages = event.get("messages", [])
        #     yield {
        #         "event": "messages",
        #         "messages": messages,
        #     }
        parts = self.graph.stream(
            {"messages": [("user", user_input)]},
            self.build_config(session_id),
            stream_mode=["messages", "updates"],
            version="v2",
        )
        for part in parts:
            yield part

    def has_pending_interrupt(self, session_id: str) -> bool:
        snapshot = self.get_snapshot(session_id)
        return bool(snapshot.next)

    def stream_approval(self, session_id: str, approved: bool, feedback: str = ""):
        snapshot = self.get_snapshot(session_id)

        if not snapshot.next:
            return

        config = self.build_config(session_id)

        if approved:
            parts = self.graph.stream(None, config, stream_mode=["messages", "updates"], version="v2")
        else:
            tool_call_id = snapshot.values["messages"][-1].tool_calls[0]["id"]
            feedback = feedback or "用户未提供原因"
            parts = self.graph.stream(
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

        for part in parts:
                yield part

    def get_snapshot(self, session_id: str) -> StateSnapshot:
        return self.graph.get_state(self.build_config(session_id))
    
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

