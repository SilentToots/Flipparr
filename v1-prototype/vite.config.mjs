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
      // it -- with SONICBOOM_API_ORIGIN rather than editing this file.
      "/api": process.env.SONICBOOM_API_ORIGIN || "http://127.0.0.1:8787",
    },
    warmup: {
      clientFiles: ["./src/main.jsx"],
    },
  },
  plugins: [react()],
});
