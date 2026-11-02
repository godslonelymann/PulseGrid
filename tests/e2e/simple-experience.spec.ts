import { operatorSession } from "./operator-session";
import { test, expect } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { resolve } from "node:path";

test("URL-only setup, advanced settings, a flat service list and email alerts", async ({
  page,
}) => {
  execFileSync(
    resolve("../../.venv/bin/python"),
    [
      "-c",
      `
import asyncio
from pulsegrid.auth.security import hasher
from pulsegrid.storage.db import Session, engine
from pulsegrid.storage.models import User
async def main():
    assert engine.url.database == "pulsegrid_test"
    async with Session.begin() as session:
        session.add(User(email="simple.browser@example.com", password_hash=hasher.hash("browser-test-password-123")))
    await engine.dispose()
asyncio.run(main())
  `,
    ],
    {
      cwd: "../..",
      env: { ...process.env, PYTHONPATH: "apps/api" },
    },
  );
  await operatorSession(
    page,
    "simple.browser@example.com",
    "browser-test-password-123",
  );
  await expect(page.getByRole("heading", { name: "Dashboard." })).toBeVisible();
  await expect(
    page
      .getByRole("navigation", { name: "Main navigation" })
      .getByRole("button"),
  ).toHaveText(["Dashboard", "Alerts", "Settings"]);
  await page.getByRole("button", { name: "Add service", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Add service", exact: true });
  await expect(dialog.getByLabel("HTTP method")).toBeHidden();
  await expect(dialog.getByLabel("Group (optional)")).toHaveCount(0);
  await dialog
    .getByLabel("Website or API URL")
    .fill("http://127.0.0.1:18080/health");
  await dialog.getByRole("button", { name: "Start monitoring" }).click();
  await expect(
    page.getByRole("button", { name: /^127.0.0.1 http/ }),
  ).toBeVisible();
  const created = (await (await page.request.get("/api/v1/dashboard")).json())
    .monitors;
  expect(created).toHaveLength(1);
  expect(created[0]).not.toHaveProperty("bucket_id");
  await page
    .getByRole("button", { name: "Edit 127.0.0.1", exact: true })
    .click();
  await page.getByLabel("Name (optional)").fill("My API");
  await page.getByText("Advanced settings", { exact: true }).click();
  await page.getByLabel("Check frequency (seconds)").fill("60");
  await page.getByLabel("Wait for a response (seconds)").fill("3");
  await page.getByLabel("HTTP method").selectOption("HEAD");
  await page.getByRole("button", { name: "Save changes" }).click();
  await page.getByRole("button", { name: "Edit My API", exact: true }).click();
  await page.getByText("Advanced settings", { exact: true }).click();
  await expect(page.getByLabel("Check frequency (seconds)")).toHaveValue("60");
  await expect(page.getByLabel("HTTP method")).toHaveValue("HEAD");
  await page.getByRole("button", { name: "Close dialog" }).click();
  await page.getByRole("button", { name: /^My API http/ }).click();
  await expect(
    page.getByRole("heading", { name: "Response time", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Latency distribution" }),
  ).toBeHidden();
  await page.getByText("More details", { exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Latency distribution" }),
  ).toBeVisible();
  await page
    .getByRole("navigation", { name: "Main navigation" })
    .getByRole("button", { name: "Dashboard", exact: true })
    .click();
  await expect(page.getByRole("heading", { name: "Dashboard." })).toBeVisible();
  await page.screenshot({
    path: "../../verification/simple-dashboard-desktop.png",
    fullPage: false,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(
    page
      .getByRole("navigation", { name: "Main navigation" })
      .getByRole("button", { name: "Alerts", exact: true }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
  await page.screenshot({
    path: "../../verification/simple-dashboard-mobile.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "Add service", exact: true }).click();
  await page.screenshot({
    path: "../../verification/simple-add-service-mobile.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "Close dialog" }).click();
  await page.getByRole("button", { name: "Alerts", exact: true }).click();
  await expect(page.getByLabel("Your account email")).toHaveValue(
    "simple.browser@example.com",
  );
  await expect(page.getByLabel("Your account email")).toHaveAttribute(
    "readonly",
    "",
  );
  await page.getByRole("button", { name: "Save email alerts" }).click();
  await expect(
    page.getByText("Email alert settings saved.", { exact: false }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Delivery history" }),
  ).toBeHidden();
  const channels = await (
    await page.request.get("/api/v1/notification-channels")
  ).json();
  expect(channels).toHaveLength(1);
  expect(channels[0]).toMatchObject({
    kind: "email",
    email: "simple.browser@example.com",
  });
  await page.reload();
  await expect(page.getByLabel("Your account email")).toHaveValue(
    "simple.browser@example.com",
  );
  await page.screenshot({
    path: "../../verification/simple-alerts-mobile.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "Dashboard", exact: true }).click();
  await page.getByRole("button", { name: "Add service", exact: true }).click();
  await page
    .getByLabel("Website or API URL")
    .fill("http://127.0.0.1:18080/other");
  await page.getByRole("button", { name: "Start monitoring" }).click();
  await expect(
    page.getByRole("button", { name: /^127.0.0.1 http/ }),
  ).toBeVisible();
  const services = (await (await page.request.get("/api/v1/dashboard")).json())
    .monitors;
  expect(services).toHaveLength(2);
  for (const service of services)
    expect(service).not.toHaveProperty("bucket_id");
});

test("dashboard puts problems first and distinguishes missing checks from downtime", async ({
  page,
}) => {
  let online = true;
  const services = ["UP", "PAUSED", "UNKNOWN", "SUSPECT", "DOWN"].map(
    (state, index) => ({
      id: String(index),
      name: state,
      url: `https://example.com/${index}`,
      state,
      method: "GET",
      interval: 30,
      timeout: 10,
      statuses: [200],
      paused: state === "PAUSED",
      last_observed: null,
      observed_state: state,
    }),
  );
  await page.route("**/api/v1/**", (route) => {
    const path = new URL(route.request().url()).pathname;
    const json = path.endsWith("/me")
      ? {
          email: "owner@example.com",
          id: "owner",
          password_enabled: true,
          google_connected: false,
          mfa_enabled: false,
          session_id: "session",
        }
      : path.endsWith("/dashboard")
        ? { monitors: services, counts: {} }
        : path.endsWith("/worker-status")
          ? { workers: online ? [{ id: "worker", stale: false }] : [] }
          : [];
    return route.fulfill({ json });
  });
  await page.goto("/?view=dashboard&bucket=old-group");
  await expect(page.getByText("Manage groups", { exact: false })).toHaveCount(
    0,
  );
  await expect(page.getByLabel("Filter by group")).toHaveCount(0);
  await expect(page.locator(".service-list .monitor-link strong")).toHaveText([
    "DOWN",
    "SUSPECT",
    "UNKNOWN",
    "UP",
    "PAUSED",
  ]);
  await expect(
    page
      .locator(".service-row")
      .filter({ hasText: "UNKNOWN" })
      .locator(".badge"),
  ).toHaveText("No recent checks");
  await expect(
    page.locator(".service-row").filter({ hasText: "DOWN" }).locator(".badge"),
  ).toHaveText("Down");
  await expect(
    page.getByText("2 services need attention", { exact: true }),
  ).toBeVisible();
  online = false;
  await page.reload();
  await expect(
    page.getByText("Monitoring has stopped", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("All services are working", { exact: true }),
  ).toHaveCount(0);
  online = true;
  services.forEach((service) => {
    service.state = "PAUSED";
    service.paused = false;
  });
  await page.reload();
  await expect(
    page.getByText("All services are paused", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Resume UP", exact: true }),
  ).toBeEnabled();
  await page.route("**/api/v1/dashboard", (route) =>
    route.fulfill({
      status: 503,
      json: { detail: "Service data is temporarily unavailable" },
    }),
  );
  await page.reload();
  await expect(
    page.getByText("Service status is unavailable", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("We couldn’t load your services.", { exact: false }),
  ).toBeVisible();
});
