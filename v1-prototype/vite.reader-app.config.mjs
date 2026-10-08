// Flipparr Reader, the iPad and iPhone app: a second, smaller build of the
// same source (docs/OFFLINE_READING_PLAN.md). Its entry is reader-app/ and
// it is written to dist/reader-app, which Capacitor copies into the app.
// `npm run build` (the web app the Docker image serves) does not build it.
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  root: "reader-app",
  // Relative, because the app is served from capacitor://localhost/, not a
  // site root a web server controls.
  base: "./",
  build: {
    outDir: "../dist/reader-app",
    emptyOutDir: true,
  },
  plugins: [react()],
});
