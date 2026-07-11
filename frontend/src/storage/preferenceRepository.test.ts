import { describe, expect, it } from "vitest";
import type { KeyValueStorage } from "./keyValueStorage";
import { createPreferenceRepository } from "./preferenceRepository";


class MemoryStorage implements KeyValueStorage {
  readonly values = new Map<string, string>();

  getItem(key: string) {
    return this.values.get(key) ?? null;
  }

  setItem(key: string, value: string) {
    this.values.set(key, value);
  }

  removeItem(key: string) {
    this.values.delete(key);
  }
}


describe("preference repository", () => {
  it("accepts only the persisted light theme and defaults everything else", () => {
    const storage = new MemoryStorage();
    const repository = createPreferenceRepository(storage);

    expect(repository.loadTheme()).toBe("dark");
    storage.setItem("tech-doc-agent.theme", "solarized");
    expect(repository.loadTheme()).toBe("dark");
    storage.setItem("tech-doc-agent.theme", "light");
    expect(repository.loadTheme()).toBe("light");
  });

  it("persists theme changes through the storage port", () => {
    const storage = new MemoryStorage();
    const repository = createPreferenceRepository(storage);

    expect(repository.saveTheme("light")).toBe(true);
    expect(repository.loadTheme()).toBe("light");
  });
});
