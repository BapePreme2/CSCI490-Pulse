import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// `vitest/config`'s defineConfig extends Vite's with a `test` field, so one
// config file covers both the dev server and the test runner.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173 },
  test: {
    environment: "jsdom",
    setupFiles: "./src/setupTests.ts",
  },
});
