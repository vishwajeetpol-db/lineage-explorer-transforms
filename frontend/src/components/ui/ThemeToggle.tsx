import { Moon, Sun } from "lucide-react";
import { useThemeStore } from "../../store/themeStore";

/** Dark/light theme toggle. Drop into a top-right corner. */
export default function ThemeToggle({ className = "" }: { className?: string }) {
  const theme = useThemeStore((s) => s.theme);
  const toggle = useThemeStore((s) => s.toggleTheme);
  const isDark = theme === "dark";
  return (
    <button
      onClick={toggle}
      title={isDark ? "Switch to light mode" : "Switch to dark mode"}
      aria-label="Toggle theme"
      className={`flex items-center justify-center w-8 h-8 rounded-lg bg-surface-100/60 hover:bg-surface-200 border border-white/[0.08] hover:border-accent/40 text-slate-400 hover:text-accent-light transition-all ${className}`}
    >
      {isDark ? <Sun size={15} /> : <Moon size={15} />}
    </button>
  );
}
