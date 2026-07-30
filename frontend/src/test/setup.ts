import "@testing-library/jest-dom/vitest";
import { afterEach, vi } from "vitest";
import { cleanup } from "@testing-library/react";

// Unmount React trees + reset mocks between tests so each is hermetic.
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

// jsdom lacks matchMedia (used by the theme store) — provide a stub.
if (!window.matchMedia) {
  window.matchMedia = ((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })) as unknown as typeof window.matchMedia;
}

// jsdom in this Node/vitest combo doesn't expose a working localStorage — provide
// an in-memory shim so stores that persist (themeStore, useRecents) work.
if (!("localStorage" in globalThis) || typeof globalThis.localStorage?.clear !== "function") {
  const _store = new Map<string, string>();
  const mem = {
    getItem: (k: string) => (_store.has(k) ? _store.get(k)! : null),
    setItem: (k: string, v: string) => void _store.set(k, String(v)),
    removeItem: (k: string) => void _store.delete(k),
    clear: () => _store.clear(),
    key: (i: number) => Array.from(_store.keys())[i] ?? null,
    get length() { return _store.size; },
  };
  Object.defineProperty(globalThis, "localStorage", { value: mem, configurable: true, writable: true });
}

// jsdom doesn't implement pointer capture (used by DraggablePanel drag handlers).
if (typeof Element !== "undefined") {
  if (!Element.prototype.setPointerCapture) Element.prototype.setPointerCapture = () => {};
  if (!Element.prototype.releasePointerCapture) Element.prototype.releasePointerCapture = () => {};
  if (!Element.prototype.hasPointerCapture) Element.prototype.hasPointerCapture = () => false;
  // jsdom lacks scrollIntoView (some list/tree components call it).
  if (!Element.prototype.scrollIntoView) Element.prototype.scrollIntoView = () => {};
}

// jsdom lacks ResizeObserver (React Flow / some panels reference it).
if (!(globalThis as any).ResizeObserver) {
  (globalThis as any).ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
}
