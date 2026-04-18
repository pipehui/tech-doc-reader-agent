import uuid
import os
os.environ["LANGCHAIN_TRACING_V2"] = "false"
os.environ.pop("LANGCHAIN_API_KEY", None)

from customer_support_chat.app.graph import build_multi_agentic_graph
from customer_support_chat.app.core.logger import logger
from customer_support_chat.app.core.settings import get_settings
from langchain_core.messages import ToolMessage
from langgraph.checkpoint.redis import RedisSaver

def main():
    # Generate a unique thread ID for the session
    settings = get_settings()

    session_id = input("请输入会话ID，留空则新建：").strip()
    if not session_id:
        session_id = str(uuid.uuid4())

    print(f"当前会话ID: {session_id}")

    config = {
        "configurable": {
            "thread_id": session_id,
        }
    }
    with RedisSaver.from_conn_string(settings.REDIS_URL) as checkpointer:
        checkpointer.setup()
        graph = build_multi_agentic_graph(checkpointer)
        printed_message_ids = set()

        snapshot = graph.get_state(config)
        state_value = getattr(snapshot, "values", None)

        if state_value and "messages" in state_value:
            for message in state_value["messages"]:
                if getattr(message, "id", None):
                    printed_message_ids.add(message.id)

            if state_value["messages"]:
                print(f"已恢复会话，共加载 {len(state_value['messages'])} 条历史消息。")


        try:
            while True:
                user_input = input("User: ")
                if user_input.strip().lower() in ["quit", "exit", "q"]:
                    print("Goodbye!")
                    break

                events = graph.stream(
                    {"messages": [("user", user_input)]}, config, stream_mode="values"
                )

                for event in events:
                    messages = event.get("messages", [])
                    for message in messages:
                        if message.id not in printed_message_ids:
                            message.pretty_print()
                            printed_message_ids.add(message.id)

                # Check for interrupts (sensitive tool approval)
                snapshot = graph.get_state(config)
                while snapshot.next:
                    user_input = input(
                        "\n是否批准以上操作？输入 'y' 继续，否则请说明你的修改意见。\n\n"
                    )
                    if user_input.strip().lower() == "y":
                        resume_events = graph.stream(None, config, stream_mode="values")
                    else:
                        tool_call_id = snapshot.values["messages"][-1].tool_calls[0]["id"]
                        resume_events = graph.stream(
                            {
                                "messages": [
                                    ToolMessage(
                                        tool_call_id=tool_call_id,
                                        content=f"用户拒绝了此操作。原因：'{user_input}'。请根据用户的反馈继续协助。",
                                    )
                                ]
                            },
                            config,
                            stream_mode="values",
                        )
                    for event in resume_events:
                        messages = event.get("messages", [])
                        for message in messages:
                            if message.id not in printed_message_ids:
                                message.pretty_print()
                                printed_message_ids.add(message.id)

                    snapshot = graph.get_state(config)

        except Exception as e:
            logger.error(f"An error occurred: {e}")
            print(f"发生错误：{e}")

if __name__ == "__main__":
    main()