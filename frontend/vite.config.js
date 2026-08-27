import { defineConfig } from "vite";

export default defineConfig({
  test: { setupFiles: ["./src/test/setup.js"], restoreMocks: true },
});
