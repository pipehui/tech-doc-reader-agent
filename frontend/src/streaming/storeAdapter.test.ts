import { describe, expect, it, vi } from "vitest";
import { dispatchStreamActions } from "./storeAdapter";
import type { StreamActionTarget } from "./storeAdapter";
import type { StreamAction } from "./sseReducer";


function target() {
  return {
    recordEvent: vi.fn(),
    setSessionState: vi.fn(),
    updateStreamingMessage: vi.fn(),
    addToolCall: vi.fn(),
    updateToolResult: vi.fn()
  } satisfies StreamActionTarget;
}


describe("stream store adapter", () => {
  it("dispatches reducer actions in order", () => {
    const sink = target();
    const toolCall = {
      id: "call-1",
      agent: "parser" as const,
      tool: "read_docs",
      args: {},
      result: "",
      status: "pending" as const,
      createdAt: "now",
      updatedAt: "now"
    };
    const actions: StreamAction[] = [
      {
        type: "record_event",
        event: {
          type: "tool_call",
          data: {},
          agent: "parser",
          responseId: "response-1"
        }
      },
      { type: "set_session_state", state: { current_agent: "parser" } },
      {
        type: "update_streaming_message",
        responseId: "response-1",
        agent: "parser",
        text: "chunk"
      },
      { type: "add_tool_call", toolCall, responseId: "response-1" },
      { type: "update_tool_result", toolCall, responseId: "response-1" }
    ];

    dispatchStreamActions(actions, sink);

    expect(sink.recordEvent).toHaveBeenCalledOnce();
    expect(sink.setSessionState).toHaveBeenCalledWith({ current_agent: "parser" });
    expect(sink.updateStreamingMessage).toHaveBeenCalledWith(
      "response-1",
      "parser",
      "chunk",
      undefined
    );
    expect(sink.addToolCall).toHaveBeenCalledWith(toolCall, "response-1");
    expect(sink.updateToolResult).toHaveBeenCalledWith(toolCall, "response-1");
  });

  it("warns for unknown events in development and ignores them", () => {
    const warning = vi.spyOn(console, "warn").mockImplementation(() => undefined);

    dispatchStreamActions(
      [{
        type: "protocol_warning",
        event: "future_event",
        data: { value: 1 },
        reason: "unknown_event"
      }],
      target()
    );

    expect(warning).toHaveBeenCalledWith(
      "Ignoring unknown SSE event: future_event",
      { value: 1 }
    );
    warning.mockRestore();
  });

  it("distinguishes invalid known payloads from unknown future events", () => {
    const warning = vi.spyOn(console, "warn").mockImplementation(() => undefined);

    dispatchStreamActions(
      [{
        type: "protocol_warning",
        event: "token",
        data: {},
        reason: "invalid_payload",
        error: "token.text must be a string"
      }],
      target()
    );

    expect(warning).toHaveBeenCalledWith(
      "Invalid SSE payload for token: token.text must be a string",
      {}
    );
    warning.mockRestore();
  });

  it("throws explicit stream errors after prior actions are dispatched", () => {
    const sink = target();
    expect(() => dispatchStreamActions(
      [
        {
          type: "record_event",
          event: {
            type: "error",
            data: { message: "failed" },
            agent: "primary",
            responseId: "response-1"
          }
        },
        { type: "stream_error", message: "failed" }
      ],
      sink
    )).toThrow("failed");
    expect(sink.recordEvent).toHaveBeenCalledOnce();
  });
});
