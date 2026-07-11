import type { StateCreator } from "zustand";
import type { PreferenceRepository } from "../storage/preferenceRepository";
import type { AppStore, UiSlice } from "./contracts";


export interface UiSliceDependencies {
  preferenceRepository: PreferenceRepository;
}


export function createUiSlice(
  dependencies: UiSliceDependencies
): StateCreator<AppStore, [], [], UiSlice> {
  const { preferenceRepository } = dependencies;

  return (set, get) => ({
    running: false,
    runLabel: "就绪",
    error: "",
    theme: preferenceRepository.loadTheme(),
    expandedToolIds: new Set(),

    setRunning(running, label = "生成中") {
      set({
        running,
        runLabel: running ? label : "就绪",
        error: running ? "" : get().error
      });
    },

    setError(message) {
      set({ running: false, runLabel: "就绪", error: message });
      get().addSystemMessage(message);
    },

    toggleToolExpanded(id) {
      set((state) => {
        const expandedToolIds = new Set(state.expandedToolIds);
        if (expandedToolIds.has(id)) expandedToolIds.delete(id);
        else expandedToolIds.add(id);
        return { expandedToolIds };
      });
    },

    setTheme(theme) {
      preferenceRepository.saveTheme(theme);
      set({ theme });
    }
  });
}
