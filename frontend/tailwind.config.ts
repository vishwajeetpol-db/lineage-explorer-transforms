import type { Config } from "tailwindcss";

export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        // Theme-aware tokens — backed by CSS variables (RGB channels, so
        // Tailwind opacity modifiers like bg-surface-100/60 still work). Values
        // are defined per-theme in styles/globals.css (:root/.dark = dark,
        // .light = inverted). This is what lets the whole UI flip with one class
        // on <html> instead of editing every component.
        surface: {
          DEFAULT: "rgb(var(--surface) / <alpha-value>)",
          50: "rgb(var(--surface-50) / <alpha-value>)",
          100: "rgb(var(--surface-100) / <alpha-value>)",
          200: "rgb(var(--surface-200) / <alpha-value>)",
          300: "rgb(var(--surface-300) / <alpha-value>)",
          400: "rgb(var(--surface-400) / <alpha-value>)",
        },
        // Override Tailwind's built-in slate + white so existing text-slate-*/
        // text-white/border-white classes become theme-aware automatically.
        white: "rgb(var(--white) / <alpha-value>)",
        slate: {
          100: "rgb(var(--slate-100) / <alpha-value>)",
          200: "rgb(var(--slate-200) / <alpha-value>)",
          300: "rgb(var(--slate-300) / <alpha-value>)",
          400: "rgb(var(--slate-400) / <alpha-value>)",
          500: "rgb(var(--slate-500) / <alpha-value>)",
          600: "rgb(var(--slate-600) / <alpha-value>)",
          700: "rgb(var(--slate-700) / <alpha-value>)",
          800: "rgb(var(--slate-800) / <alpha-value>)",
          900: "rgb(var(--slate-900) / <alpha-value>)",
        },
        accent: {
          DEFAULT: "#6366F1",
          light: "#818CF8",
          dark: "#4F46E5",
          glow: "rgba(99,102,241,0.3)",
        },
        purple: {
          glow: "rgba(139,92,246,0.3)",
        },
        node: {
          table: "#3B82F6",
          view: "#10B981",
          mv: "#F59E0B",
        },
      },
      fontFamily: {
        // System font stack — Databricks Apps CSP blocks fonts.googleapis.com,
        // so we use the OS's native UI font (San Francisco on macOS, Segoe UI
        // on Windows, etc.) instead of trying to load Inter/JetBrains Mono.
        sans: ["system-ui", "-apple-system", "BlinkMacSystemFont", "Segoe UI", "Helvetica Neue", "Arial", "sans-serif"],
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "Monaco", "Consolas", "Liberation Mono", "Courier New", "monospace"],
      },
      animation: {
        "flow": "flow 2s linear infinite",
        "glow-pulse": "glow-pulse 2s ease-in-out infinite",
        "fade-in": "fade-in 0.2s ease-out",
        "scale-in": "scale-in 0.15s ease-out",
        "slide-down": "slide-down 0.2s ease-out",
        "shimmer": "shimmer 2s linear infinite",
      },
      keyframes: {
        flow: {
          "0%": { strokeDashoffset: "24" },
          "100%": { strokeDashoffset: "0" },
        },
        "glow-pulse": {
          "0%, 100%": { opacity: "0.4" },
          "50%": { opacity: "1" },
        },
        "fade-in": {
          "0%": { opacity: "0" },
          "100%": { opacity: "1" },
        },
        "scale-in": {
          "0%": { opacity: "0", transform: "scale(0.95)" },
          "100%": { opacity: "1", transform: "scale(1)" },
        },
        "slide-down": {
          "0%": { opacity: "0", transform: "translateY(-4px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        shimmer: {
          "0%": { backgroundPosition: "-200% 0" },
          "100%": { backgroundPosition: "200% 0" },
        },
      },
      boxShadow: {
        glow: "0 0 20px rgba(99,102,241,0.15)",
        "glow-lg": "0 0 40px rgba(99,102,241,0.2)",
        "node": "0 4px 24px rgba(0,0,0,0.4)",
        "node-hover": "0 8px 32px rgba(0,0,0,0.5), 0 0 20px rgba(99,102,241,0.1)",
      },
    },
  },
  plugins: [],
} satisfies Config;
