import react from "@vitejs/plugin-react";
import svgr from "vite-plugin-svgr";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react(), svgr({ svgrOptions: { icon: true, svgProps: { fill: "currentColor" } } })],
  server: {
    port: 5173,
    // `make dev`: the backend runs on 8080; the browser only talks to Vite.
    proxy: { "/api": "http://localhost:8080", "/files": "http://localhost:8080" },
  },
  build: {
    outDir: "dist",
    // Never inline assets as data: URLs; the app's CSP only allows files from itself.
    assetsInlineLimit: 0,
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    css: false,
  },
});
