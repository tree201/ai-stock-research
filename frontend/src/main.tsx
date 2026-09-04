import React from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";
import App from "./App";

// Apply the saved skin before the first paint to avoid a theme flash.
document.documentElement.dataset.theme =
  localStorage.getItem("theme") === "dark" ? "dark" : "light";

createRoot(document.getElementById("root")!).render(
  <React.StrictMode><App /></React.StrictMode>,
);
