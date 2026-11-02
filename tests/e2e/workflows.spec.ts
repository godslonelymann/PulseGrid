import { operatorSession } from "./operator-session";
import { test, expect } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import { readFileSync, writeFileSync } from "node:fs";

test("owner MFA, monitoring, outage/recovery, charts, cross-tab refresh and revocation", async ({
  page,
  context,
  request,
}) => {
  execFileSync(
    resolve("../../.venv/bin/python"),
    ["-m", "pulsegrid.manage", "recover", "browser@example.com"],
    {
      input: "browser-test-password-123\nbrowser-test-password-123\n",
      cwd: "../..",
      env: { ...process.env, PYTHONPATH: "apps/api" },
    },
  );
  await operatorSession(
    page,
    "browser@example.com",
    "browser-test-password-123",
  );
  await expect(page.getByRole("heading", { name: "Dashboard." })).toBeVisible();
  await page.getByRole("button", { name: "Settings" }).click();
  await page
    .getByLabel("Current password")
    .first()
    .fill("browser-test-password-123");
  await page.getByRole("button", { name: "Set up authenticator" }).click();
  const secret = await page.locator("code.secret").innerText();
  const code = execFileSync("../../.venv/bin/python", [
    "-c",
    "import pyotp,sys; print(pyotp.TOTP(sys.argv[1]).now())",
    secret,
  ])
    .toString()
    .trim();
  await page.getByLabel("Six-digit authenticator code").fill(code);
  await page.getByRole("button", { name: "Confirm enrollment" }).click();
  await expect(page.locator(".recovery-codes code")).toHaveCount(10);
  const codes = await page.locator(".recovery-codes code").allTextContents();
  expect(codes).toHaveLength(10);
  await page.getByRole("button", { name: "I have saved these" }).click();
  await page.getByRole("button", { name: "Sign out", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Welcome to PulseGrid." }),
  ).toBeVisible();
  await operatorSession(
    page,
    "browser@example.com",
    "browser-test-password-123",
  );
  await expect(
    page.getByRole("heading", { name: "One more check." }),
  ).toBeVisible();
  await page.getByLabel("Authentication or recovery code").fill(codes[0]);
  await page.getByRole("button", { name: "Verify and sign in" }).click();
  await page.getByRole("button", { name: "Dashboard", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Dashboard." })).toBeVisible();
  await page.getByRole("button", { name: "Add service", exact: true }).click();
  await page.getByLabel("Name (optional)").fill("Fixture API");
  await page.getByText("Advanced settings", { exact: true }).click();
  await page
    .getByLabel("Website or API URL")
    .fill("http://127.0.0.1:18080/health");
  await page.getByLabel("Wait for a response (seconds)").fill("1");
  await page.getByRole("button", { name: "Start monitoring" }).click();
  await expect(
    page.getByRole("button", { name: /^Fixture API http/ }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Alerts", exact: true }).click();
  await page.getByText("Advanced alert settings", { exact: true }).click();
  await page.getByRole("button", { name: "Add channel" }).click();
  await page.getByLabel("Name", { exact: true }).fill("Fixture webhook");
  await page.getByLabel("Webhook URL").fill("http://127.0.0.1:18080/hook");
  await page.getByRole("button", { name: "Save channel" }).click();
  await page.getByRole("button", { name: "Send test", exact: true }).click();
  await expect(
    page.getByText("Delivered", { exact: true }).first(),
  ).toBeVisible();
  await page.getByRole("button", { name: "Dashboard", exact: true }).click();
  await page.getByRole("button", { name: /^Fixture API http/ }).click();
  await expect(
    page.getByRole("heading", { name: "Response time" }),
  ).toBeVisible();
  await expect(page.locator(".badge.up").first()).toBeVisible();
  // Expiry is deliberately 8s: two visible tabs must coordinate their refreshes.
  const tab = await context.newPage();
  await tab.goto("/");
  await page.waitForTimeout(9000);
  await Promise.all([page.reload(), tab.reload()]);
  await expect(tab.getByRole("heading", { name: "Dashboard." })).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Fixture API", exact: true }),
  ).toBeVisible();
  await tab.close();
  await request.post("http://127.0.0.1:18080/control/503");
  await page.getByRole("button", { name: "Dashboard", exact: true }).click();
  await page.getByRole("button", { name: /^Fixture API http/ }).click();
  await expect(page.locator(".page-heading .badge.down")).toBeVisible({
    timeout: 75000,
  });
  await expect
    .poll(
      async () => {
        const r = await request.get("http://127.0.0.1:18080/events");
        return (await r.json()).some(
          (e: { event_kind: string }) => e.event_kind === "outage",
        );
      },
      { timeout: 20000 },
    )
    .toBeTruthy();
  const workerState = JSON.parse(
    readFileSync("../../verification/e2e-worker-state.json", "utf8"),
  );
  writeFileSync(
    "../../verification/e2e-worker.restart",
    "restart after outage notification",
  );
  await expect
    .poll(
      () =>
        JSON.parse(
          readFileSync("../../verification/e2e-worker-state.json", "utf8"),
        ).restarts,
    )
    .toBe(workerState.restarts + 1);
  await page.getByRole("button", { name: "Pause", exact: true }).click();
  await expect(page.locator(".page-heading .badge.paused")).toBeVisible();
  await page.getByRole("button", { name: "Resume", exact: true }).click();
  await expect(page.locator(".page-heading .badge.unknown")).toBeVisible();
  await request.post("http://127.0.0.1:18080/control/200");
  await expect(page.locator(".page-heading .badge.up")).toBeVisible({
    timeout: 75000,
  });
  await expect
    .poll(
      async () => {
        const r = await request.get("http://127.0.0.1:18080/events");
        return (await r.json()).some(
          (e: { event_kind: string }) => e.event_kind === "recovery",
        );
      },
      { timeout: 20000 },
    )
    .toBeTruthy();
  await page.screenshot({
    path: "../../verification/dashboard-desktop.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(
    page.getByRole("heading", { name: "Fixture API", exact: true }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBeTruthy();
  await page.screenshot({
    path: "../../verification/dashboard-mobile.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 1280, height: 900 });
  const revokedTab = await context.newPage();
  await revokedTab.goto("/");
  await expect(
    revokedTab.getByRole("heading", { name: "Dashboard." }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Settings" }).click();
  await page.getByRole("button", { name: "Revoke", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Welcome to PulseGrid." }),
  ).toBeVisible();
  await expect(
    revokedTab.getByRole("heading", { name: "Welcome to PulseGrid." }),
  ).toBeVisible();
  await revokedTab.close();
});
