import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// API proxy target: defaults to vault service on :7778
const apiPort = process.env.VITE_VAULT_API_PORT ?? "7778";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": `http://localhost:${apiPort}`,
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
  },
});
