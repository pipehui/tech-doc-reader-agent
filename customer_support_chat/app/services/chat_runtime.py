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
            stream_mode="messages",
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
            parts = self.graph.stream(None, config, stream_mode="messages", version="v2")
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
                stream_mode="messages",
                version="v2",
            )

        for part in parts:
                yield part

    def get_snapshot(self, session_id: str) -> StateSnapshot:
        return self.graph.get_state(self.build_config(session_id))