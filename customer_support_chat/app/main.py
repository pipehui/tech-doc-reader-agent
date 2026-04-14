import uuid
import os
os.environ["LANGCHAIN_TRACING_V2"] = "false"
os.environ.pop("LANGCHAIN_API_KEY", None)

from customer_support_chat.app.graph import multi_agentic_graph
from customer_support_chat.app.core.logger import logger
from langchain_core.messages import ToolMessage

def main():
    # Generate a unique thread ID for the session
    thread_id = str(uuid.uuid4())

    config = {
        "configurable": {
            "thread_id": thread_id,
        }
    }

    printed_message_ids = set()

    try:
        while True:
            user_input = input("User: ")
            if user_input.strip().lower() in ["quit", "exit", "q"]:
                print("Goodbye!")
                break

            events = multi_agentic_graph.stream(
                {"messages": [("user", user_input)]}, config, stream_mode="values"
            )

            for event in events:
                messages = event.get("messages", [])
                for message in messages:
                    if message.id not in printed_message_ids:
                        message.pretty_print()
                        printed_message_ids.add(message.id)

            # Check for interrupts (sensitive tool approval)
            snapshot = multi_agentic_graph.get_state(config)
            while snapshot.next:
                user_input = input(
                    "\n是否批准以上操作？输入 'y' 继续，否则请说明你的修改意见。\n\n"
                )
                if user_input.strip().lower() == "y":
                    result = multi_agentic_graph.invoke(None, config)
                else:
                    tool_call_id = snapshot.value["messages"][-1].tool_calls[0]["id"]
                    result = multi_agentic_graph.invoke(
                        {
                            "messages": [
                                ToolMessage(
                                    tool_call_id=tool_call_id,
                                    content=f"用户拒绝了此操作。原因：'{user_input}'。请根据用户的反馈继续协助。",
                                )
                            ]
                        },
                        config,
                    )
                messages = result.get("messages", [])
                for message in messages:
                    if message.id not in printed_message_ids:
                        message.pretty_print()
                        printed_message_ids.add(message.id)

                snapshot = multi_agentic_graph.get_state(config)

    except Exception as e:
        logger.error(f"An error occurred: {e}")
        print(f"发生错误：{e}")

if __name__ == "__main__":
    main()