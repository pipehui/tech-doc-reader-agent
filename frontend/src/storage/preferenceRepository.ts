import { readStorage, writeStorage } from "./keyValueStorage";
import type {
  KeyValueStorage,
  StorageFailureHandler
} from "./keyValueStorage";


const THEME_KEY = "tech-doc-agent.theme";

export type Theme = "dark" | "light";

export interface PreferenceRepository {
  loadTheme(): Theme;
  saveTheme(theme: Theme): boolean;
}


export function createPreferenceRepository(
  storage: KeyValueStorage,
  onFailure?: StorageFailureHandler
): PreferenceRepository {
  return {
    loadTheme() {
      return readStorage(storage, THEME_KEY, onFailure) === "light"
        ? "light"
        : "dark";
    },

    saveTheme(theme) {
      return writeStorage(storage, THEME_KEY, theme, onFailure);
    }
  };
}
