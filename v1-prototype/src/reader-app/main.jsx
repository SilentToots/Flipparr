import React from "react";
import { createRoot } from "react-dom/client";
import "@fontsource-variable/inter/opsz.css";
import "@fontsource-variable/inter/opsz-italic.css";
import "../styles.css";
import { ReaderApp } from "./ReaderApp.jsx";

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <ReaderApp />
  </React.StrictMode>,
);
