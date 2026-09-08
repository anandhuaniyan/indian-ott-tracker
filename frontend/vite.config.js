import { defineConfig, loadEnv } from "vite";

export default defineConfig(({ mode }) => {
  const env = { ...loadEnv(mode, process.cwd(), "VITE_"), ...process.env };
  const client = env.VITE_ADSENSE_CLIENT_ID || "";
  return {
  plugins: [{
    name: "adsense-ownership-verification",
    transformIndexHtml() {
      // Supported ownership verification without cookies or script execution.
      return /^ca-pub-\d{16}$/.test(client) ? [{
        tag: "meta", attrs: { name: "google-adsense-account", content: client }, injectTo: "head",
      }] : [];
    },
  }],
  test: { setupFiles: ["./src/test/setup.js"], restoreMocks: true },
  server: {
    host: true,
    proxy: {
      "/api": "http://localhost:8000",
      "/media": "http://localhost:8000",
      "/storage": "http://localhost:8000",
    },
  },
};
});
