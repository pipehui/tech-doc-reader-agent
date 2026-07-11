import type { StateCreator } from "zustand";
import type { AppStore, LearningSlice } from "./contracts";
import { createInitialLearning } from "./defaults";


export function createLearningSlice(): StateCreator<
  AppStore,
  [],
  [],
  LearningSlice
> {
  return (set) => ({
    learning: createInitialLearning(),
    showLearnerPlan: false,

    setLearning(learning) {
      set({ learning });
    },

    setShowLearnerPlan(showLearnerPlan) {
      set({ showLearnerPlan });
    }
  });
}
