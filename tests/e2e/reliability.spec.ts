import { expect, test } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import { operatorSession } from "./operator-session";

const email = "reliability.browser@example.com";
const password = "reliability-test-password-123";

test.beforeAll(() => {
  execFileSync(
    resolve("../../.venv/bin/python"),
    [
      "-c",
      `
import asyncio
import sys
from pulsegrid.auth.security import hasher
from pulsegrid.storage.db import Session, engine
from pulsegrid.storage.models import User

async def main():
    assert engine.url.database == "pulsegrid_test"
    async with Session.begin() as session:
        session.add(User(email=sys.argv[1], password_hash=hasher.hash(sys.argv[2])))
    await engine.dispose()

asyncio.run(main())
`,
      email,
      password,
    ],
    { cwd: "../..", env: { ...process.env, PYTHONPATH: "apps/api" } },
  );
});

test("a failed test email is visible in Settings", async ({ page }) => {
  await operatorSession(page, email, password);
  // Expose the test button while the test worker runs without an SMTP server.
  await page.route("**/api/v1/notification-settings", async (route) => {
    if (route.request().method() !== "GET") return route.continue();
    const response = await route.fetch();
    await route.fulfill({
      response,
      json: { ...(await response.json()), email_available: true },
    });
  });
  await page.getByRole("button", { name: "Your account settings" }).click();
  const alerts = page.getByRole("region", { name: "Email alerts" });
  await expect(alerts.getByText("No tests sent yet.")).toBeVisible();
  const preference = alerts.getByRole("switch");
  await preference.click();
  await expect(preference).toBeChecked();
  await alerts.getByRole("button", { name: "Send test email" }).click();
  await expect(page.getByText("Test queued", { exact: true })).toBeVisible();
  await expect(alerts.getByRole("status")).toContainText(
    "Failed. Another attempt is scheduled.",
  );
});

test("long response time windows render the trend", async ({ page }) => {
  await operatorSession(page, email, password);
  await page.getByRole("button", { name: "Add service", exact: true }).click();
  await page.getByLabel("Name (optional)").fill("Response time fixture");
  await page
    .getByLabel("Website or API URL")
    .fill("http://127.0.0.1:18080/health");
  await page.getByRole("button", { name: "Start monitoring" }).click();
  const recent = Array.from({ length: 20 }, (_, index) => {
    const observed_at = new Date(Date.now() - index * 30_000).toISOString();
    return {
      id: String(index),
      observed_at,
      scheduled_for: observed_at,
      success: index % 2 === 0,
      status_code: index % 2 === 0 ? 200 : 503,
      latency_ms: 18,
      lag_ms: 12,
      error: index % 2 === 0 ? null : "Unexpected status",
    };
  });
  await page.route("**/api/v1/monitors/*/results?*", (route) => {
    const params = new URL(route.request().url()).searchParams;
    const start = Number(params.get("offset"));
    return route.fulfill({
      json: recent.slice(start, start + Number(params.get("limit"))),
    });
  });
  await page
    .getByRole("button", { name: /^Response time fixture http/ })
    .click();
  const window = page.getByRole("group", { name: "Metric window" });
  const chart = page.locator(".chart-panel").filter({
    has: page.getByRole("heading", { name: "Response time", exact: true }),
  });
  for (const [hours, bucket] of [
    ["7d", "1 hr"],
    ["30d", "6 hr"],
  ]) {
    await window.getByRole("button", { name: hours, exact: true }).click();
    await expect(
      chart.getByText(
        `Median time to response headers for successful checks · per ${bucket}`,
      ),
    ).toBeVisible();
    await expect(chart.locator(".recharts-area-dot").first()).toBeVisible();
  }
  const surface = chart.getByRole("application");
  await surface.focus();
  await page.keyboard.press("ArrowRight");
  const tooltip = chart.locator(".recharts-tooltip-wrapper");
  await expect(tooltip).toBeVisible();
  await page.waitForResponse((response) =>
    response.url().includes("/metrics?hours=720"),
  );
  await expect(surface).toBeFocused();
  await expect(tooltip).toBeVisible();

  const strip = page.getByRole("list", {
    name: "Observed checks, oldest to newest",
  });
  const firstCheck = strip.getByRole("button").first();
  await firstCheck.click();
  const selected = await firstCheck.getAttribute("aria-label");
  await page.getByText("More details", { exact: true }).click();
  const pagination = page.getByRole("navigation", {
    name: "Check diagnostics pagination",
  });
  await pagination.getByRole("button", { name: "Next", exact: true }).click();
  await expect(pagination.getByText("Page 2", { exact: true })).toBeVisible();
  await expect(
    strip.getByRole("button", { name: selected!, pressed: true }),
  ).toBeVisible();
  await page.locator(".check-diagnostics summary").first().click();
  await expect(
    page.locator(".check-diagnostics").getByText("HTTP status").first(),
  ).toBeVisible();
});
