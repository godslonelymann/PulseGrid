import { defineConfig, devices } from "@playwright/test";
export default defineConfig({
  testDir: ".",
  workers: 1,
  timeout: 90000,
  expect: { timeout: 20000 },
  use: {
    baseURL: "https://localhost:8443",
    ignoreHTTPSErrors: true,
    trace: "retain-on-failure",
  },
  outputDir: "../../verification/staging-browser-artifacts",
  reporter: [
    ["list"],
    ["json", { outputFile: "../../verification/staging-browser-results.json" }],
  ],
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
