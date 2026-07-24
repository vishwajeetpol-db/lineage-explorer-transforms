import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import ErrorBoundary from "./components/ui/ErrorBoundary";
import "./store/themeStore"; // applies persisted theme class to <html> before first paint
import "./styles/globals.css";

// Global error surfacing — React error boundaries don't catch errors thrown
// inside event handlers, async callbacks, or promise rejections. Log them
// to the console with a clear marker so they show up in DevTools instead
// of being lost.
window.addEventListener("error", (e) => {
  console.error("[unhandled error]", e.error || e.message);
});
window.addEventListener("unhandledrejection", (e) => {
  console.error("[unhandled promise rejection]", e.reason);
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </React.StrictMode>
);
