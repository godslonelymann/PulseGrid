import { defineConfig, devices } from "@playwright/test";
export default defineConfig({
  testDir: ".",
  testMatch: "*.spec.ts",
  workers: 1,
  timeout: 240000,
  expect: { timeout: 15000 },
  use: {
    baseURL: "http://localhost:3001",
    actionTimeout: 15000,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  outputDir: "../../verification/browser-artifacts",
  reporter: [
    ["list"],
    ["json", { outputFile: "../../verification/browser-results.json" }],
  ],
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
