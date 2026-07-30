import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// Vitest config for the BrickTrace frontend. jsdom environment + React Testing
// Library. Coverage gate at 90%, excluding the graph/canvas rendering layer
// (React Flow / ELK / web-worker) which is impractical to unit-test.
export default defineConfig({
  plugins: [react()],
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    css: false,
    coverage: {
      provider: "v8",
      reporter: ["text", "text-summary", "json-summary", "html"],
      reportsDirectory: "./coverage",
      include: ["src/**/*.{ts,tsx}"],
      exclude: [
        "src/main.tsx",
        "src/vite-env.d.ts",
        "src/test/**",
        "src/stubs/**",
        // Graph / canvas rendering layer — React Flow + ELK layout, not unit-testable.
        "src/components/graph/**",
        "src/components/transform/TransformCanvas.tsx",
        "src/components/transform/TransformNode.tsx",
        "src/components/transform/TransformEdge.tsx",
        "src/lib/elkLayout.ts",
        // Type-only / re-export barrels.
        "src/components/transform/index.ts",
        "**/*.d.ts",
      ],
      thresholds: {
        lines: 90,
        functions: 90,
        branches: 85,
        statements: 90,
      },
    },
  },
});
