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
  ).toHaveText(["Dashboard"]);
  await expect(
    page.getByRole("heading", { name: "No active services" }),
  ).toBeVisible();
  const sidebar = page.locator("#workspace-sidebar");
  const dashboardLink = sidebar.getByRole("button", { name: "Dashboard" });
  const label = dashboardLink.getByText("Dashboard", { exact: true });
  await expect(label).toBeHidden();
  await expect(sidebar.getByRole("button")).toHaveCount(2);
  const sidebarWidth = async () => (await sidebar.boundingBox())!.width;
  const railWidth = await sidebarWidth();
  await sidebar.hover({ position: { x: 20, y: 40 } });
  await expect(label).toBeVisible();
  await expect.poll(sidebarWidth).toBeGreaterThan(railWidth);
  await page.getByRole("heading", { name: "Dashboard." }).hover();
  await expect(label).toBeHidden();
  await expect.poll(sidebarWidth).toBe(railWidth);

  await page.keyboard.press("Tab");
  await expect(
    sidebar.getByRole("link", { name: "PulseGrid home" }),
  ).toBeFocused();
  await expect(label).toBeVisible();
  await page.keyboard.press("Tab");
  await expect(dashboardLink).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(
    sidebar.getByRole("button", { name: "Your account settings" }),
  ).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(label).toBeHidden();
  await page.getByRole("button", { name: "Add service", exact: true }).click();
  const addService = page.getByRole("button", {
    name: "Add service",
    exact: true,
  });
  await expect(addService).toHaveCSS("height", "44px");
  const dialog = page.getByRole("dialog", { name: "Add service", exact: true });
  await expect(
    dialog.getByRole("button", { name: "Start monitoring" }),
  ).toHaveCSS("height", "44px");
  const closeButton = dialog.getByRole("button", { name: "Close dialog" });
  await expect(closeButton).toHaveCSS("height", "44px");
  await expect(closeButton).toHaveCSS("width", "44px");
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
  const firstActions = page.getByRole("button", {
    name: "Actions for 127.0.0.1",
    exact: true,
  });
  await firstActions.focus();
  await page.keyboard.press("Enter");
  await page.keyboard.press("Tab");
  await expect(
    page.getByRole("button", { name: "Edit 127.0.0.1", exact: true }),
  ).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(firstActions).toBeFocused();
  await firstActions.click();
  await page
    .getByRole("button", { name: "Edit 127.0.0.1", exact: true })
    .click();
  await page.getByLabel("Name (optional)").fill("My API");
  await page.getByText("Advanced settings", { exact: true }).click();
  await page.getByLabel("Check frequency (seconds)").fill("60");
  await expect(page.getByLabel("Wait for a response (seconds)")).toHaveCount(0);
  await expect(page.getByLabel("HTTP method")).toHaveCount(0);
  await expect(page.getByLabel("Successful response codes")).toHaveCount(0);
  await page.getByRole("button", { name: "Save changes" }).click();
  await page
    .getByRole("button", { name: "Actions for My API", exact: true })
    .click();
  await page.getByRole("button", { name: "Edit My API", exact: true }).click();
  await page.getByText("Advanced settings", { exact: true }).click();
  await expect(page.getByLabel("Check frequency (seconds)")).toHaveValue("60");
  await page.getByRole("button", { name: "Close dialog" }).click();
  const renamedActions = page.getByRole("button", {
    name: "Actions for My API",
    exact: true,
  });
  await expect(renamedActions).toBeFocused();
  await renamedActions.click();
  await page.getByRole("button", { name: "Pause My API", exact: true }).click();
  await expect(page.getByText("Service paused", { exact: true })).toBeVisible();
  await renamedActions.click();
  await page
    .getByRole("button", { name: "Resume My API", exact: true })
    .click();
  await expect(
    page.getByText("Service resumed", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: /^My API http/ }).click();
  await expect(
    page.getByRole("heading", { name: "Response time", exact: true }),
  ).toBeVisible();
  const latency = page.getByRole("heading", { name: "Latency distribution" });
  const detailsHeader = page
    .locator("summary")
    .filter({ hasText: "More details" });
  await expect(latency).toBeHidden();
  await detailsHeader.focus();
  await page.keyboard.press("Enter");
  await expect(latency).toBeVisible();
  await detailsHeader.locator(".accordion-chevron").click();
  await expect(latency).toBeHidden();
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
    page.getByRole("button", { name: "Your account settings" }),
  ).toBeVisible();
  const menuButton = page.getByRole("button", { name: "Open navigation menu" });
  const menu = page.getByRole("dialog", { name: "Navigation menu" });
  await expect(menu).toBeHidden();
  await menuButton.click();
  await expect(menu.getByRole("button", { name: "Dashboard" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(menu).toBeHidden();
  await expect(menuButton).toBeFocused();
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
    animations: "disabled",
  });
  await page.getByRole("button", { name: "Close dialog" }).click();
  await page.getByRole("button", { name: "Your account settings" }).click();
  const emailAlerts = page.getByRole("region", { name: "Email alerts" });
  await expect(
    emailAlerts.getByText("simple.browser@example.com", { exact: false }),
  ).toBeVisible();
  const emailSwitch = emailAlerts.getByRole("switch");
  await expect(emailSwitch).not.toBeChecked();
  await emailSwitch.click();
  await expect(emailSwitch).toBeChecked();
  await page.reload();
  await expect(emailSwitch).toBeChecked();
  await emailSwitch.click();
  await expect(emailSwitch).not.toBeChecked();
  await menuButton.click();
  await menu.getByRole("button", { name: "Dashboard" }).click();
  await expect(menu).toBeHidden();
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
  let releaseDashboard!: () => void;
  const firstLoad = new Promise<void>((resolve) => {
    releaseDashboard = resolve;
  });
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/dashboard")) await firstLoad;
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
  await expect(page.locator(".skeleton-row")).toHaveCount(3);
  releaseDashboard();
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
  const unknownActions = page.getByRole("button", {
    name: "Actions for UNKNOWN",
    exact: true,
  });
  const rows = page.locator(".service-list .monitor-link strong");
  await unknownActions.focus();
  services[2].state = "DOWN";
  await expect(
    page.getByText("3 services need attention", { exact: true }),
  ).toBeVisible();
  await expect(unknownActions).toBeFocused();
  await expect(rows).toHaveText(["DOWN", "SUSPECT", "UNKNOWN", "UP", "PAUSED"]);
  await page.getByRole("textbox", { name: "Search services" }).focus();
  await expect(rows).toHaveText(["DOWN", "UNKNOWN", "SUSPECT", "UP", "PAUSED"]);
  services[2].state = "UNKNOWN";
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
    service.paused = true;
  });
  await page.reload();
  await expect(
    page.getByText("All services are paused", { exact: true }),
  ).toBeVisible();
  const actions = page.getByRole("button", {
    name: "Actions for UP",
    exact: true,
  });
  await actions.click();
  await expect(
    page.getByRole("button", { name: "Resume UP", exact: true }),
  ).toBeEnabled();
  await page.keyboard.press("Escape");
  await page
    .getByRole("textbox", { name: "Search services" })
    .fill("no matches");
  await expect(
    page.getByRole("heading", { name: "No search matches" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Clear search" }).click();
  await expect(page.locator(".service-list > li")).toHaveCount(5);
  await page.setViewportSize({ width: 320, height: 550 });
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.getByRole("button", { name: "Add service", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Add service", exact: true });
  await expect(dialog.getByLabel("Website or API URL")).toBeFocused();
  expect(
    await dialog.evaluate((element) => getComputedStyle(element).animationName),
  ).toBe("none");
  await page.keyboard.press("Escape");
  await expect(
    page.getByRole("button", { name: "Add service", exact: true }),
  ).toBeFocused();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.route("**/api/v1/dashboard", (route) =>
    route.fulfill({
      status: 503,
      json: { detail: "Service data is temporarily unavailable" },
    }),
  );
  await expect(
    page.getByText("Service status could not refresh.", { exact: false }),
  ).toBeVisible();
  await expect(page.locator(".service-list > li")).toHaveCount(5);
  await page.reload();
  await expect(
    page.getByText("Service status is unavailable", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("We couldn’t load your services", { exact: false }),
  ).toBeVisible();
});
