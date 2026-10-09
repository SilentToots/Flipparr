import React from "react";
import { createRoot } from "react-dom/client";
import "@fontsource-variable/inter/opsz.css";
import "@fontsource-variable/inter/opsz-italic.css";
import "../styles.css";
import { ReaderApp } from "./ReaderApp.jsx";
import { AppErrorBoundary } from "./ErrorBoundary.jsx";

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <AppErrorBoundary><ReaderApp /></AppErrorBoundary>
  </React.StrictMode>,
);
