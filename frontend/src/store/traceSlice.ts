import type { StateCreator } from "zustand";
import type { TraceEvent } from "../types";
import type { AppStore, TraceSlice } from "./contracts";
import { EVENT_TYPES } from "./defaults";


export interface TraceSliceDependencies {
  createId: () => string;
  now: () => string;
}


export function createTraceSlice(
  dependencies: TraceSliceDependencies
): StateCreator<AppStore, [], [], TraceSlice> {
  const { createId, now } = dependencies;

  return (set, get) => ({
    events: [],
    selectedEventId: null,
    filters: new Set(EVENT_TYPES),
    recording: true,
    inspectorPaused: false,
    replayingEventId: null,

    recordEvent(event) {
      if (!get().recording || event.type === "token") return;
      set((state) => {
        const next: TraceEvent = {
          ...event,
          id: createId(),
          seq: state.events.length + 1,
          timestamp: event.timestamp || now()
        };
        return {
          events: [...state.events, next].slice(-3000),
          selectedEventId: state.selectedEventId || next.id
        };
      });
      get().persistTranscript();
    },

    setSelectedEventId(selectedEventId) {
      set({ selectedEventId });
    },

    toggleFilter(eventType) {
      set((state) => {
        const filters = new Set(state.filters);
        if (filters.has(eventType)) filters.delete(eventType);
        else filters.add(eventType);
        return { filters };
      });
    },

    setRecording(recording) {
      set({ recording });
    },

    setInspectorPaused(inspectorPaused) {
      set({ inspectorPaused });
    },

    setReplayingEventId(replayingEventId) {
      set({ replayingEventId });
    }
  });
}
