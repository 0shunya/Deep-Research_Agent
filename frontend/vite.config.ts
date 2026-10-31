/// <reference types="vitest/config" />
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  return {
    plugins: [react(), tailwindcss()],
    server: {
      port: 5183,
      // In development the browser calls /api on the Vite server, which forwards to FastAPI.
      proxy: { "/api": env.VITE_DEV_API_TARGET || "http://localhost:8020" },
    },
    build: { sourcemap: false },
    test: {
      environment: "jsdom",
      setupFiles: ["./src/test/setup.ts"],
      css: false,
    },
  };
});
