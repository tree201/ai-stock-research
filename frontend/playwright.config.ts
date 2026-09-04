import { defineConfig } from "@playwright/test";

/**
 * Theme e2e suite. The backend is started automatically against a scratch
 * database so the suite never touches ./research.sqlite3; set E2E_BASE_URL
 * to reuse an already running server instead.
 */
export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  use: {
    baseURL: process.env.E2E_BASE_URL || "http://127.0.0.1:8011",
    channel: "chrome",
  },
  webServer: process.env.E2E_BASE_URL
    ? undefined
    : {
        command:
          "PYTHONPATH=src AI_STOCK_DB=/tmp/ai-stock-e2e.sqlite3 python3 -m stock_research --web --port 8011",
        port: 8011,
        reuseExistingServer: true,
        cwd: "..",
        timeout: 30_000,
      },
});
