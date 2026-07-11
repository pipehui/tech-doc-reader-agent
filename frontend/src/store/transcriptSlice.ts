import type { StateCreator } from "zustand";
import { normalizeAgent } from "../agentColors";
import type { TranscriptRepository } from "../storage/transcriptRepository";
import { sessionTenant } from "../tenant";
import type { ChatMessage } from "../types";
import type { AppStore, TranscriptSlice } from "./contracts";


export interface TranscriptSliceDependencies {
  transcriptRepository: TranscriptRepository;
  createId: () => string;
  now: () => string;
}


export function createTranscriptSlice(
  dependencies: TranscriptSliceDependencies
): StateCreator<AppStore, [], [], TranscriptSlice> {
  const { transcriptRepository, createId, now } = dependencies;

  return (set, get) => ({
    messages: [],
    toolCalls: {},
    hasNewMessageContent: false,

    setMessages(messages) {
      set({ messages });
      get().persistTranscript();
    },

    addSystemMessage(content) {
      set((state) => ({
        messages: [
          ...state.messages,
          createMessage(createId(), now(), "system", content)
        ]
      }));
      get().persistTranscript();
    },

    addUserMessage(content) {
      set((state) => ({
        messages: [
          ...state.messages,
          createMessage(createId(), now(), "user", content)
        ],
        hasNewMessageContent: true
      }));
      get().persistTranscript();
    },

    updateStreamingMessage(responseId, agent, text, finalContent) {
      const normalized = normalizeAgent(agent);
      set((state) => {
        const messages = [...state.messages];
        let index = messages.findIndex(
          (message) => message.responseId === responseId
            && message.agent === normalized
            && message.role === "assistant"
        );
        if (index === -1) {
          messages.push({
            id: createId(),
            role: "assistant",
            agent: normalized,
            content: "",
            streaming: true,
            responseId,
            toolCallIds: [],
            createdAt: now()
          });
          index = messages.length - 1;
        }
        const current = messages[index];
        messages[index] = {
          ...current,
          content: finalContent ?? `${current.content}${text}`,
          streaming: finalContent === undefined
        };
        return { messages, hasNewMessageContent: true };
      });
    },

    finishResponse(responseId) {
      set((state) => ({
        messages: state.messages
          .map((message) => message.responseId === responseId
            ? { ...message, streaming: false }
            : message)
          .filter((message) => message.role !== "assistant"
            || message.responseId !== responseId
            || Boolean(message.content.trim())
            || Boolean(message.toolCallIds.length))
      }));
      get().persistTranscript();
    },

    addToolCall(toolCall, responseId) {
      set((state) => {
        const messages = [...state.messages];
        if (responseId) {
          let index = messages.findIndex(
            (message) => message.responseId === responseId
              && message.agent === toolCall.agent
              && message.role === "assistant"
          );
          if (index === -1) {
            messages.push({
              id: createId(),
              role: "assistant",
              agent: toolCall.agent,
              content: "",
              streaming: true,
              responseId,
              toolCallIds: [],
              createdAt: now()
            });
            index = messages.length - 1;
          }
          const current = messages[index];
          messages[index] = {
            ...current,
            toolCallIds: current.toolCallIds.includes(toolCall.id)
              ? current.toolCallIds
              : [...current.toolCallIds, toolCall.id]
          };
        }
        return {
          messages,
          toolCalls: { ...state.toolCalls, [toolCall.id]: toolCall },
          hasNewMessageContent: true
        };
      });
    },

    updateToolResult(toolCall, responseId) {
      get().addToolCall(toolCall, responseId);
    },

    setHasNewMessageContent(hasNewMessageContent) {
      set({ hasNewMessageContent });
    },

    hydrateTranscript(sessionId, tenant) {
      const parsed = transcriptRepository.load(
        sessionId,
        tenant || sessionTenant(get().session)
      );
      if (!parsed) return false;
      set({
        messages: parsed.messages,
        events: parsed.events.filter((event) => event.type !== "token"),
        toolCalls: parsed.toolCalls
      });
      return Boolean(parsed.messages.length || parsed.events.length);
    },

    persistTranscript() {
      const state = get();
      transcriptRepository.save(
        state.session.session_id,
        sessionTenant(state.session),
        {
          messages: state.messages,
          events: state.events,
          toolCalls: state.toolCalls
        }
      );
    }
  });
}


function createMessage(
  id: string,
  createdAt: string,
  role: "system" | "user",
  content: string
): ChatMessage {
  return {
    id,
    role,
    agent: "primary",
    content,
    streaming: false,
    toolCallIds: [],
    createdAt
  };
}
