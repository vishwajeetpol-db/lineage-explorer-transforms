import { useCallback, useEffect, useState } from "react";

const STORAGE_KEY = "bricktrace:recents";
const MAX_RECENTS = 10;

function readFromStorage(): string[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((x) => typeof x === "string" && x.split(".").length === 3);
  } catch {
    return [];
  }
}

function writeToStorage(recents: string[]) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(recents));
  } catch {
    // Ignore quota / disabled storage
  }
}

export function useRecents() {
  const [recents, setRecents] = useState<string[]>(() => readFromStorage());

  // Sync across tabs / windows
  useEffect(() => {
    const onStorage = (e: StorageEvent) => {
      if (e.key === STORAGE_KEY) setRecents(readFromStorage());
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  const addRecent = useCallback((fqdn: string) => {
    if (!fqdn || fqdn.split(".").length !== 3) return;
    setRecents((prev) => {
      const next = [fqdn, ...prev.filter((x) => x !== fqdn)].slice(0, MAX_RECENTS);
      writeToStorage(next);
      return next;
    });
  }, []);

  const clearRecents = useCallback(() => {
    writeToStorage([]);
    setRecents([]);
  }, []);

  return { recents, addRecent, clearRecents };
}
