import { create } from "zustand";
import { resolveBrowserStorage } from "./storage/keyValueStorage";
import type {
  KeyValueStorage,
  StorageFailure,
  StorageFailureHandler
} from "./storage/keyValueStorage";
import { createPreferenceRepository } from "./storage/preferenceRepository";
import type { PreferenceRepository } from "./storage/preferenceRepository";
import { createSessionRepository } from "./storage/sessionRepository";
import type { SessionRepository } from "./storage/sessionRepository";
import { createTranscriptRepository } from "./storage/transcriptRepository";
import type { TranscriptRepository } from "./storage/transcriptRepository";
import type { AppStore } from "./store/contracts";
import { createLearningSlice } from "./store/learningSlice";
import { createSessionSlice } from "./store/sessionSlice";
import { createTraceSlice } from "./store/traceSlice";
import { createTranscriptSlice } from "./store/transcriptSlice";
import { createUiSlice } from "./store/uiSlice";
import { makeSessionId, uid } from "./utils";


export { EVENT_TYPES } from "./store/defaults";
export type { AppStore } from "./store/contracts";
export type { SessionEntry } from "./storage/sessionRepository";

export interface AppStoreDependencies {
  storage: KeyValueStorage;
  sessionRepository: SessionRepository;
  preferenceRepository: PreferenceRepository;
  transcriptRepository: TranscriptRepository;
  onStorageFailure: StorageFailureHandler;
  createId: () => string;
  createSessionId: () => string;
  now: () => string;
}


export function createAppStore(
  dependencies: Partial<AppStoreDependencies> = {}
) {
  const storage = dependencies.storage || resolveBrowserStorage();
  const onStorageFailure = dependencies.onStorageFailure || reportStorageFailure;
  const createId = dependencies.createId || uid;
  const createSessionId = dependencies.createSessionId || makeSessionId;
  const now = dependencies.now || (() => new Date().toISOString());
  const sessionRepository = dependencies.sessionRepository
    || createSessionRepository(storage, onStorageFailure, {
      createSessionId,
      now
    });
  const preferenceRepository = dependencies.preferenceRepository
    || createPreferenceRepository(storage, onStorageFailure);
  const transcriptRepository = dependencies.transcriptRepository
    || createTranscriptRepository(storage, onStorageFailure);
  const initialContext = sessionRepository.loadContext();

  return create<AppStore>((set, get, store) => ({
    ...createSessionSlice({
      initialContext,
      sessionRepository,
      transcriptRepository,
      createSessionId,
      now
    })(set, get, store),
    ...createTranscriptSlice({ transcriptRepository, createId, now })(
      set,
      get,
      store
    ),
    ...createTraceSlice({ createId, now })(set, get, store),
    ...createLearningSlice()(set, get, store),
    ...createUiSlice({ preferenceRepository })(set, get, store)
  }));
}


function reportStorageFailure(failure: StorageFailure) {
  if (import.meta.env.DEV) {
    console.warn(
      `Browser storage ${failure.operation} failed for ${failure.key}`,
      failure.error
    );
  }
}


export const useAppStore = createAppStore();
