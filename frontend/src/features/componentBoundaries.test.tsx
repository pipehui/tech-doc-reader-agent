// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { ApprovalDrawer } from "./approval/ApprovalDrawer";
import {
  MessageBubble,
  PlanStepper
} from "./chat/ChatPane";
import { InspectorToolbar } from "./inspector/Inspector";
import { EVENT_TYPES, useAppStore, type AppStore } from "../store";
import type { ChatMessage, ToolCall } from "../types";


const streamMocks = vi.hoisted(() => ({
  approve: vi.fn(),
  send: vi.fn()
}));

vi.mock("../useChatStream", () => ({
  useChatStream: () => streamMocks
}));


const INITIAL_STATE = useAppStore.getInitialState();
const NOW = "2026-07-12T00:00:00.000Z";


function resetStore(overrides: Partial<AppStore> = {}) {
  useAppStore.setState({
    ...INITIAL_STATE,
    session: {
      ...INITIAL_STATE.session,
      session_id: "component-session",
      user_id: "component-user",
      namespace: "component-tests",
      pending_interrupt: false,
      workflow_plan: [],
      plan_index: 0
    },
    sessions: [],
    messages: [],
    toolCalls: {},
    events: [],
    filters: new Set(EVENT_TYPES),
    expandedToolIds: new Set(),
    ...overrides
  }, true);
}


beforeEach(() => {
  streamMocks.approve.mockReset();
  streamMocks.send.mockReset();
  resetStore();
});

afterEach(() => {
  cleanup();
});


describe("feature component boundaries", () => {
  it("renders the latest pending approval and submits reject feedback", () => {
    const older: ToolCall = {
      id: "call-old",
      agent: "parser",
      tool: "read_docs",
      args: {},
      status: "pending",
      createdAt: "2026-07-11T00:00:00.000Z",
      updatedAt: "2026-07-11T00:00:00.000Z"
    };
    const latest: ToolCall = {
      ...older,
      id: "call-latest",
      agent: "primary",
      tool: "save_docs",
      createdAt: NOW,
      updatedAt: NOW
    };
    resetStore({
      session: {
        ...useAppStore.getState().session,
        pending_interrupt: true
      },
      toolCalls: { [older.id]: older, [latest.id]: latest }
    });

    const { container } = render(<ApprovalDrawer />);

    const drawer = container.querySelector(".approval-drawer");
    expect(drawer?.classList.contains("hidden")).toBe(false);
    expect(screen.getByText("primary 请求执行 save_docs")).toBeTruthy();

    fireEvent.change(screen.getByPlaceholderText("拒绝时可填写反馈，例如：换一种检索范围"), {
      target: { value: "只读，不保存" }
    });
    fireEvent.click(screen.getByRole("button", { name: "拒绝" }));

    expect(streamMocks.approve).toHaveBeenCalledWith(false, "只读，不保存");
  });

  it("renders plan done/current/queued states from the session slice", () => {
    resetStore({
      session: {
        ...useAppStore.getState().session,
        workflow_plan: ["parser", "explanation", "summary"],
        plan_index: 1
      }
    });

    render(<PlanStepper />);

    const steps = screen.getAllByRole("button");
    expect(steps).toHaveLength(3);
    expect(steps[0].classList.contains("done")).toBe(true);
    expect(steps[0].textContent).toContain("parser");
    expect(steps[1].classList.contains("current")).toBe(true);
    expect(steps[1].textContent).toContain("active");
    expect(steps[2].textContent).toContain("queued");
  });

  it("renders markdown, agent breaks and explicit tool error state", () => {
    const tool: ToolCall = {
      id: "call-error",
      agent: "parser",
      tool: "read_docs",
      args: { query: "StateGraph" },
      result: "offline",
      status: "error",
      createdAt: NOW,
      updatedAt: NOW
    };
    const previous: ChatMessage = {
      id: "message-previous",
      role: "assistant",
      agent: "relation",
      content: "previous",
      streaming: false,
      toolCallIds: [],
      createdAt: NOW
    };
    const message: ChatMessage = {
      id: "message-current",
      role: "assistant",
      agent: "parser",
      content: "**hello**",
      streaming: false,
      toolCallIds: [tool.id],
      createdAt: NOW
    };
    resetStore({ toolCalls: { [tool.id]: tool } });

    const { container } = render(
      <MessageBubble message={message} previous={previous} />
    );

    expect(container.querySelector("article")?.classList.contains("agent-break"))
      .toBe(true);
    expect(screen.getByText("hello").tagName).toBe("STRONG");
    expect(screen.getByText("read_docs")).toBeTruthy();
    expect(screen.getByText("错误")).toBeTruthy();
  });

  it("toggles Inspector filters through the trace slice", () => {
    render(<InspectorToolbar />);

    const filter = screen.getByRole("button", { name: "tool_result" });
    expect(filter.classList.contains("active")).toBe(true);

    fireEvent.click(filter);

    expect(useAppStore.getState().filters.has("tool_result")).toBe(false);
    expect(filter.classList.contains("active")).toBe(false);
  });
});
