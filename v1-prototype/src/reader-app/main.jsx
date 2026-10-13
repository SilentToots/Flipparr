import React from "react";
import { createRoot } from "react-dom/client";
import "@fontsource-variable/inter/opsz.css";
import "@fontsource-variable/inter/opsz-italic.css";
import "../styles.css";
import { ReaderApp } from "./ReaderApp.jsx";
import { AppErrorBoundary } from "./ErrorBoundary.jsx";
import { startEdgeBack } from "./edge-back.js";

// iOS's edge swipe closes the drawer or sheet in front (edge-back.js).
try { startEdgeBack(); } catch { /* not in the app: no native side */ }

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <AppErrorBoundary><ReaderApp /></AppErrorBoundary>
  </React.StrictMode>,
);
