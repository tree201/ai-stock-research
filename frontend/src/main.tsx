import React from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";
import App from "./App";
import { applyTheme, initialTheme } from "./theme";

// Apply the saved skin before the first paint to avoid a theme flash.
applyTheme(initialTheme());

createRoot(document.getElementById("root")!).render(
  <React.StrictMode><App /></React.StrictMode>,
);
