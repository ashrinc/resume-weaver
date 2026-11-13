"use client";

import { useCallback, useSyncExternalStore } from "react";

// localStorage-backed state that is safe for server rendering and hydration.
//
// useSyncExternalStore renders `fallback` on the server and during hydration,
// then switches to the stored value, so there is no hydration mismatch and no
// setState-in-useEffect. An in-memory copy keeps things working when storage
// is unavailable or full (private browsing, quota exceeded).

const memory = new Map<string, string>();
const listeners = new Set<() => void>();

function notify(): void {
  listeners.forEach((listener) => listener());
}

function read(key: string): string | null {
  if (memory.has(key)) return memory.get(key) ?? null;
  try {
    const value = window.localStorage.getItem(key);
    if (value !== null) memory.set(key, value);
    return value;
  } catch {
    return null;
  }
}

function write(key: string, value: string | null): void {
  if (value === null) memory.delete(key);
  else memory.set(key, value);
  try {
    if (value === null) window.localStorage.removeItem(key);
    else window.localStorage.setItem(key, value);
  } catch {
    // Storage unavailable or full: the in-memory copy still works this session.
  }
  notify();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  const onStorage = (event: StorageEvent) => {
    // Another tab changed storage: drop our cached copy and re-read.
    if (event.key === null) memory.clear();
    else memory.delete(event.key);
    listener();
  };
  window.addEventListener("storage", onStorage);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("storage", onStorage);
  };
}

/**
 * @param serverValue what to render before the stored value is known (server
 *   render and hydration). Choose it to avoid a visual flash.
 */
export function useLocalStorage(
  key: string,
  fallback: string,
  serverValue: string = fallback,
): [string, (value: string | null) => void] {
  const value = useSyncExternalStore(
    subscribe,
    () => read(key) ?? fallback,
    () => serverValue,
  );
  const setValue = useCallback((next: string | null) => write(key, next), [key]);
  return [value, setValue];
}
