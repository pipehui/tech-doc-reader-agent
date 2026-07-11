export interface KeyValueStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

export type StorageOperation = "read" | "write" | "delete";

export interface StorageFailure {
  operation: StorageOperation;
  key: string;
  error: unknown;
}

export type StorageFailureHandler = (failure: StorageFailure) => void;


const UNAVAILABLE_STORAGE: KeyValueStorage = {
  getItem: () => null,
  setItem: () => undefined,
  removeItem: () => undefined
};

const IGNORE_FAILURE: StorageFailureHandler = () => undefined;


export function resolveBrowserStorage(): KeyValueStorage {
  try {
    return globalThis.localStorage || UNAVAILABLE_STORAGE;
  } catch {
    return UNAVAILABLE_STORAGE;
  }
}


export function readStorage(
  storage: KeyValueStorage,
  key: string,
  onFailure: StorageFailureHandler = IGNORE_FAILURE
) {
  try {
    return storage.getItem(key);
  } catch (error) {
    onFailure({ operation: "read", key, error });
    return null;
  }
}


export function writeStorage(
  storage: KeyValueStorage,
  key: string,
  value: string,
  onFailure: StorageFailureHandler = IGNORE_FAILURE
) {
  try {
    storage.setItem(key, value);
    return true;
  } catch (error) {
    onFailure({ operation: "write", key, error });
    return false;
  }
}


export function deleteStorage(
  storage: KeyValueStorage,
  key: string,
  onFailure: StorageFailureHandler = IGNORE_FAILURE
) {
  try {
    storage.removeItem(key);
    return true;
  } catch (error) {
    onFailure({ operation: "delete", key, error });
    return false;
  }
}
