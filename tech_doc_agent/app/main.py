import os
import uuid

os.environ["LANGCHAIN_TRACING_V2"] = "false"
os.environ.pop("LANGCHAIN_API_KEY", None)

from tech_doc_agent.app.core.logger import logger
from tech_doc_agent.app.services.chat_runtime import ChatRuntime

def print_new_messages(parts) -> None:
    # for event in events:
    #     if event["event"] != "messages":
    #         continue

    #     for message in event["messages"]:
    #         if getattr(message, "id", None) not in printed_message_ids:
    #             message.pretty_print()
    #             if getattr(message, "id", None):
    #                 printed_message_ids.add(message.id)
    printed_any = False

    for part in parts:
        if part["type"] != "messages":
            continue

        token, metadata = part["data"]

        text = getattr(token, "text", "")
        if text:
            print(text, end="", flush=True)
            printed_any = True

    if printed_any:
        print()

def main():
    with ChatRuntime() as runtime:
        # Generate a unique thread ID for the session
        session_id = input("请输入会话ID，留空则新建：").strip()
        if not session_id:
            session_id = str(uuid.uuid4())

        print(f"当前会话ID: {session_id}")

        snapshot = runtime.get_snapshot(session_id)
        state_value = getattr(snapshot, "values", None)
        if state_value and state_value.get("messages"):
            print(f"已恢复会话，共加载 {len(state_value['messages'])} 条历史消息。")


        try:
            while True:
                user_input = input("User: ")
                if user_input.strip().lower() in ["quit", "exit", "q"]:
                    print("Goodbye!")
                    break

                parts = runtime.stream_user_message(session_id, user_input)
                print_new_messages(parts)

                while runtime.has_pending_interrupt(session_id):
                    approval_input = input(
                        "\n是否批准以上操作？输入 'y' 继续，否则请说明你的修改意见。\n\n"
                    )

                    approved = approval_input.strip().lower() == "y"
                    feedback = "" if approved else approval_input
                    resume_events = runtime.stream_approval(session_id, approved, feedback)
                    print_new_messages(resume_events)
        except Exception as e:
            logger.error(f"An error occurred: {e}")
            print(f"发生错误：{e}")

if __name__ == "__main__":
    main()
