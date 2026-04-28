from langchain_core.messages import AIMessage, ToolMessage

from tech_doc_agent.app.api.routes.chat import iter_update_events


def test_iter_update_events_emits_plan_transition_and_tool_events():
    events = list(
        iter_update_events(
            {
                "data": {
                    "store_plan": {
                        "workflow_plan": ["parser", "relation", "explanation"],
                        "plan_index": 0,
                        "learning_target": "LangGraph StateGraph",
                    },
                    "enter_parser": {},
                    "parser": {
                        "messages": [
                            AIMessage(
                                content="",
                                name="parser",
                                tool_calls=[
                                    {
                                        "name": "read_docs",
                                        "args": {"query": "LangGraph StateGraph"},
                                        "id": "call-1",
                                    }
                                ],
                            )
                        ]
                    },
                    "parser_assistant_safe_tools": {
                        "messages": [
                            ToolMessage(
                                content="[]",
                                name="read_docs",
                                tool_call_id="call-1",
                            )
                        ]
                    },
                }
            }
        )
    )

    event_names = [event.event for event in events]

    assert "plan_update" in event_names
    assert "agent_transition" in event_names
    assert "tool_call" in event_names
    assert "tool_result" in event_names
