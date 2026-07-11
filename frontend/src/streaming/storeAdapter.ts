import type { AppStore } from "../store";
import type { StreamAction } from "./sseReducer";


export type StreamActionTarget = Pick<
  AppStore,
  | "recordEvent"
  | "setSessionState"
  | "updateStreamingMessage"
  | "addToolCall"
  | "updateToolResult"
>;


export function dispatchStreamActions(
  actions: StreamAction[],
  target: StreamActionTarget
) {
  for (const action of actions) {
    switch (action.type) {
      case "record_event":
        target.recordEvent(action.event);
        break;
      case "set_session_state":
        target.setSessionState(action.state);
        break;
      case "update_streaming_message":
        target.updateStreamingMessage(
          action.responseId,
          action.agent,
          action.text,
          action.finalContent
        );
        break;
      case "add_tool_call":
        target.addToolCall(action.toolCall, action.responseId);
        break;
      case "update_tool_result":
        target.updateToolResult(action.toolCall, action.responseId);
        break;
      case "protocol_warning":
        if (import.meta.env.DEV) {
          console.warn(`Ignoring unknown SSE event: ${action.event}`, action.data);
        }
        break;
      case "stream_error":
        throw new Error(action.message);
    }
  }
}
