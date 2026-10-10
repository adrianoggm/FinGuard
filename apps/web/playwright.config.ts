import { defineConfig } from "@playwright/test";

const webPort = process.env.FINGUARD_E2E_WEB_PORT ?? "3000";
const baseURL = `http://127.0.0.1:${webPort}`;

export default defineConfig({
  testDir: "./e2e",
  testMatch: "**/*.playwright.ts",
  timeout: 120_000,
  expect: { timeout: 30_000 },
  fullyParallel: false,
  reporter: [
    ["list"],
    ["json", { outputFile: process.env.FINGUARD_E2E_REPORT ?? "test-results/report.json" }],
  ],
  use: {
    baseURL,
    browserName: "chromium",
    launchOptions: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH
      ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH }
      : {},
    trace: "retain-on-failure",
  },
  webServer: {
    command: `npm run dev -- --host 127.0.0.1 --port ${webPort}`,
    url: baseURL,
    reuseExistingServer: !process.env.CI,
    timeout: 30_000,
    env: {
      VITE_ORCHESTRATOR_URL:
        process.env.FINGUARD_E2E_ORCHESTRATOR_URL ?? "http://127.0.0.1:8000",
      VITE_ORCHESTRATOR_TOKEN: process.env.FINGUARD_E2E_TOKEN ?? "",
    },
  },
});
