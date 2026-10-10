import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import "./styles.css";

document.documentElement.dataset.finguardWebSource = __FINGUARD_WEB_SOURCE_SHA256__;

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
