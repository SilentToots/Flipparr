import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  build: {
    outDir: "dist/client",
  },
  optimizeDeps: {
    include: ["react", "react-dom/client"],
  },
  server: {
    host: "0.0.0.0",
    port: 4173,
    allowedHosts: ["terminal.local"],
    proxy: {
      // Defaults to a backend running on this machine. Point it at another
      // one -- a tunnelled QA instance, say, which has a real library behind
      // it -- with FLIPPARR_API_ORIGIN rather than editing this file. (The older
      // SONICBOOM_API_ORIGIN is still read.)
      "/api": {
        target: process.env.FLIPPARR_API_ORIGIN || process.env.SONICBOOM_API_ORIGIN || "http://127.0.0.1:8787",
        // Forward the browser's own Host. The server rejects a state-changing
        // request whose Origin does not match its Host, so rewriting Host to
        // the target makes every POST from the dev server look cross-site.
        changeOrigin: false,
      },
    },
    warmup: {
      clientFiles: ["./src/main.jsx"],
    },
  },
  plugins: [react()],
});
