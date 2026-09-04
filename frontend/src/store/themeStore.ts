import { create } from "zustand";

export type Theme = "dark" | "light";

const STORAGE_KEY = "bricktrace-theme";

function readInitial(): Theme {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved === "light" || saved === "dark") return saved;
  } catch { /* localStorage may be unavailable */ }
  return "dark"; // dark is the app's default
}

/** Apply the theme class to <html> so the CSS variables in globals.css switch. */
function applyTheme(theme: Theme) {
  const el = document.documentElement;
  el.classList.remove("dark", "light");
  el.classList.add(theme);
  el.style.colorScheme = theme;
}

interface ThemeState {
  theme: Theme;
  setTheme: (t: Theme) => void;
  toggleTheme: () => void;
}

export const useThemeStore = create<ThemeState>((set, get) => ({
  theme: readInitial(),
  setTheme: (theme) => {
    applyTheme(theme);
    try { localStorage.setItem(STORAGE_KEY, theme); } catch { /* noop */ }
    set({ theme });
  },
  toggleTheme: () => get().setTheme(get().theme === "dark" ? "light" : "dark"),
}));

// Apply the persisted theme immediately on module load (before first paint of
// components), so there's no dark→light flash.
applyTheme(readInitial());
