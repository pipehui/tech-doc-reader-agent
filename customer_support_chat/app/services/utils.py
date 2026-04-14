import os
import shutil
import sqlite3
from datetime import datetime
import pandas as pd
import requests
from customer_support_chat.app.core.settings import get_settings
from customer_support_chat.app.core.logger import logger
from qdrant_client import QdrantClient
from customer_support_chat.app.core.settings import get_settings
from typing import List, Dict, Callable, Literal

from langchain_core.messages import ToolMessage
from customer_support_chat.app.core.state import State

settings = get_settings()


def create_entry_node(assistant_name: str, new_dialog_state: str) -> Callable:
    def entry_node(state: State) -> dict:
        update = {"dialog_state": new_dialog_state}
        last_message = state["messages"][-1]
        tool_calls = getattr(last_message, "tool_calls", None)

        if tool_calls:
            tool_call_id = tool_calls[0]["id"]
            return {"messages": [
                ToolMessage(
                    content=(
                        f"You are now acting as the {assistant_name} in a multi-agent technical document learning workflow. "
                        "Review the conversation and continue the current step using the existing context and any prior intermediate results. "
                        "Follow your own role-specific instructions and use the available tools when needed. "
                        "Do not mention internal routing, workflow planning, or handoff details to the user. "
                        "If the task has changed, the current step is no longer appropriate, or you cannot continue safely, "
                        "call CompleteOrEscalate so the primary assistant can take over."
                    ),
                        tool_call_id=tool_call_id,
                    )
                ],
                "dialog_state": new_dialog_state,
            }
        
        return {
            "dialog_state": new_dialog_state,
        }
    return entry_node

def create_exit_node() -> Callable:
    def exit_node(state: State) -> dict:
        last_message = state["messages"][-1]
        tool_calls = getattr(last_message, "tool_calls", None)

        base_update = {
            "dialog_state": "pop",
            "workflow_plan": [],
            "plan_index": 0,
        }

        if tool_calls:
            handoff_call = next(
                (tc for tc in tool_calls if tc["name"] == "CompleteOrEscalate"),
                None,
            )
            if handoff_call:
                return {
                    "messages": [
                        ToolMessage(
                            content="Current step ended early. Control is returned to the primary assistant.",
                            tool_call_id=handoff_call["id"],
                        )
                    ],
                    **base_update,
                }

        return base_update

    return exit_node



def extract_last_message_text(state: State) -> str:
    last_message = state["messages"][-1]
    content = last_message.content

    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                parts.append(item.get("text", ""))
            else:
                parts.append(str(item))
        return "\n".join(part for part in parts if part).strip()
    
    return str(content)

def create_finish_node(result_key: str | None = None) -> Callable:
    def finish_node(state: State) -> dict:
        update = {
            "dialog_state": "pop",
            "plan_index": state.get("plan_index", 0) + 1,
        }

        if result_key is not None:
            update[result_key] = extract_last_message_text(state)

        return update
    return finish_node

def store_plan(state: State) -> dict:
    tool_call = state["messages"][-1].tool_calls[0]
    args = tool_call["args"]

    return {
        "messages": [
            ToolMessage(
                tool_call_id=tool_call["id"],
                content=f"Workflow plan stored: {args['steps']}",
            )
        ],
        "workflow_plan": args["steps"],
        "plan_index": 0,
        "parser_result": "",
        "relation_result": "",
        "learning_target": args["learning_target"],
    }

def download_and_prepare_db():
    settings = get_settings()
    db_file = settings.SQLITE_DB_PATH
    db_dir = os.path.dirname(db_file)
    if not os.path.exists(db_dir):
        os.makedirs(db_dir)
    db_url = "https://storage.googleapis.com/benchmarks-artifacts/travel-db/travel2.sqlite"
    if not os.path.exists(db_file):
        response = requests.get(db_url)
        response.raise_for_status()
        with open(db_file, "wb") as f:
            f.write(response.content)
        update_dates(db_file)

def update_dates(db_file):
    backup_file = db_file + '.backup'
    if not os.path.exists(backup_file):
        shutil.copy(db_file, backup_file)

    conn = sqlite3.connect(db_file)

    tables = pd.read_sql(
        "SELECT name FROM sqlite_master WHERE type='table';", conn
    ).name.tolist()
    tdf = {}
    for t in tables:
        tdf[t] = pd.read_sql(f"SELECT * from {t}", conn)

    example_time = pd.to_datetime(
        tdf["flights"]["actual_departure"].replace("\\N", pd.NaT)
    ).max()
    current_time = pd.to_datetime("now").tz_localize(example_time.tz)
    time_diff = current_time - example_time

    tdf["bookings"]["book_date"] = (
        pd.to_datetime(tdf["bookings"]["book_date"].replace("\\N", pd.NaT), utc=True)
        + time_diff
    )

    datetime_columns = [
        "scheduled_departure",
        "scheduled_arrival",
        "actual_departure",
        "actual_arrival",
    ]
    for column in datetime_columns:
        tdf["flights"][column] = (
            pd.to_datetime(tdf["flights"][column].replace("\\N", pd.NaT)) + time_diff
        )

    for table_name, df in tdf.items():
        df.to_sql(table_name, conn, if_exists="replace", index=False)

    conn.commit()
    conn.close()

def handle_tool_error(state) -> dict:
    error = state.get("error")
    tool_calls = state["messages"][-1].tool_calls
    return {
        "messages": [
            {
                "type": "tool",
                "content": f"Error: {repr(error)}\nPlease fix your mistakes.",
                "tool_call_id": tc["id"],
            }
            for tc in tool_calls
        ]
    }

def create_tool_node_with_fallback(tools: list):
    from langchain_core.messages import ToolMessage
    from langchain_core.runnables import RunnableLambda
    from langgraph.prebuilt import ToolNode

    return ToolNode(tools).with_fallbacks(
        [RunnableLambda(handle_tool_error)], exception_key="error"
    )

def get_qdrant_client():
    settings = get_settings()
    try:
        client = QdrantClient(url=settings.QDRANT_URL)
        # Test the connection
        client.get_collections()
        return client
    except Exception as e:
        logger.error(f"Failed to connect to Qdrant server at {settings.QDRANT_URL}. Error: {str(e)}")
        raise

def flight_info_to_string(flight_info: List[Dict]) -> str:
    info_lines = [] 
    i = 0
    for flight in flight_info:
        i += 1
        line = (
            f"Ticket [{i}]:\n"
            f"Ticket Number: {flight['ticket_no']}\n"
            f"Booking Reference: {flight['book_ref']}\n"
            f"Flight ID: {flight['flight_id']}\n"
            f"Flight Number: {flight['flight_no']}\n"
            f"Departure: {flight['departure_airport']} at {flight['scheduled_departure']}\n"
            f"Arrival: {flight['arrival_airport']} at {flight['scheduled_arrival']}\n"
            f"Seat: {flight['seat_no']}\n"
            f"Fare Class: {flight['fare_conditions']}\n"
            f"\n\n"
        )
        info_lines.append(line)

    info_lines = f"User current booked flight(s) details:\n" + "\n".join(info_lines)

    return "\n".join(info_lines)