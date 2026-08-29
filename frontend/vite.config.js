import { defineConfig } from "vite";

export default defineConfig({
  test: { setupFiles: ["./src/test/setup.js"], restoreMocks: true },
  server: {
    host: true,
    proxy: {
      "/api": "http://localhost:8000",
      "/media": "http://localhost:8000",
      "/storage": "http://localhost:8000",
    },
  },
});
