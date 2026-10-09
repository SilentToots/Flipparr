// Flipparr View, the iPad and iPhone app: a second, smaller build of the
// same source (docs/OFFLINE_READING_PLAN.md). Its entry is reader-app/ and
// it is written to dist/reader-app, which Capacitor copies into the app.
// `npm run build` (the web app the Docker image serves) does not build it.
import { execSync } from "node:child_process";
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
  // The repository's own files (licences, brand) ship inside the app too.
  publicDir: "../public",
  // Which build of the app this is, for Settings -> About: the commit it
  // was made from.
  define: { __VIEW_BUILD__: JSON.stringify(viewBuild()) },
});

function viewBuild() {
  try { return execSync("git rev-parse --short HEAD", { stdio: ["ignore", "pipe", "ignore"] }).toString().trim(); } catch { return "dev"; }
}
