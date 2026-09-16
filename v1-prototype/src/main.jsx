import React from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App.jsx";
// Inter, bundled rather than assumed: the design is set in it, and a phone
// without it installed fell back to the system font, which is narrower and
// broke layouts tuned in Inter. Only the subsets a page uses are fetched.
// The optical-size cut, which tightens display sizes the way the design's
// Inter does. OFL-1.1; the license ships at /licenses/Inter-OFL-1.1.txt.
import "@fontsource-variable/inter/opsz.css";
import "@fontsource-variable/inter/opsz-italic.css";
import "./styles.css";

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
