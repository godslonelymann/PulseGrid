import { operatorSession } from "./operator-session";
import { test, expect } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { resolve } from "node:path";

test("configuration, session settings and visible dependency failures", async ({
  page,
  request,
}) => {
  // Seed after the first workflow, which revoked its session and retained MFA.
  // Recover through the documented local operator command to exercise account recovery.
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
  // A clean seed also permits this test to run independently.
  await page.getByRole("button", { name: "Add service", exact: true }).click();
  await page.getByLabel("Name (optional)").fill("Editable monitor");
  await page.getByText("Advanced settings", { exact: true }).click();
  await page
    .getByLabel("Website or API URL")
    .fill("http://127.0.0.1:18080/health");
  await page.getByRole("button", { name: "Start monitoring" }).click();
  await page
    .getByRole("button", { name: "Actions for Editable monitor", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Edit Editable monitor", exact: true })
    .click();
  await page.getByLabel("Name (optional)").fill("Renamed monitor");
  await page.getByText("Advanced settings", { exact: true }).click();
  await page.getByLabel("Check frequency (seconds)").fill("45");
  await page.getByRole("button", { name: "Save changes" }).click();
  await expect(
    page.getByRole("button", { name: /^Renamed monitor http/ }),
  ).toBeVisible();
  await page.getByRole("button", { name: /^Renamed monitor http/ }).click();
  await expect(
    page.getByRole("heading", { name: "Renamed monitor", exact: true }),
  ).toBeVisible();
  await page.reload(); // monitor deep links survive reload
  await expect(
    page.getByRole("heading", { name: "Renamed monitor", exact: true }),
  ).toBeVisible();
  await page.route("**/api/v1/me", (route) =>
    route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({
        detail: "Storage dependency temporarily unavailable",
      }),
    }),
  );
  await page.reload();
  await expect(
    page
      .getByRole("alert")
      .filter({ hasText: "Storage dependency temporarily unavailable" }),
  ).toBeVisible();
  await page.unroute("**/api/v1/me");
  await page.getByRole("button", { name: "Try again" }).click();
  await expect(
    page.getByRole("heading", { name: "Renamed monitor", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Delete", exact: true }).click();
  const deletion = page.getByRole("dialog", {
    name: "Permanently delete service?",
  });
  await expect(deletion).toContainText("Renamed monitor");
  await deletion
    .getByRole("button", { name: "Delete permanently", exact: true })
    .click();
  await expect(page.getByRole("heading", { name: "Dashboard." })).toBeVisible();
  await expect(
    page.getByRole("button", { name: /^Renamed monitor http/ }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "Your account settings" }).click();
  const enrollmentPassword = page
    .getByLabel("Current password", { exact: true })
    .first();
  await enrollmentPassword.fill("browser-test-password-123");
  const visibility = page
    .getByRole("button", { name: "Show current password" })
    .first();
  await visibility.click();
  await expect(enrollmentPassword).toHaveAttribute("type", "text");
  await expect(enrollmentPassword).toHaveAttribute(
    "autocomplete",
    "current-password",
  );
  await visibility.click();
  await expect(enrollmentPassword).toHaveAttribute("type", "password");
  await page.getByRole("button", { name: "Set up authenticator" }).click();
  const secret = await page.locator("code.secret").innerText();
  const code = execFileSync("../../.venv/bin/python", [
    "-c",
    "import pyotp,sys; print(pyotp.TOTP(sys.argv[1]).now())",
    secret,
  ])
    .toString()
    .trim();
  const otp = page.getByLabel("Six-digit authenticator code");
  await expect(otp).toHaveAttribute("autocomplete", "one-time-code");
  await otp.fill(`${code.slice(0, 3)} ${code.slice(3)}`);
  await expect(otp).toHaveValue(code);
  await page.getByRole("button", { name: "Confirm enrollment" }).click();
  await expect(page.locator(".recovery-codes code")).toHaveCount(10);
  const codes = await page.locator(".recovery-codes code").allTextContents();
  await page.context().grantPermissions(["clipboard-read", "clipboard-write"]);
  await page.getByRole("button", { name: "Copy codes" }).click();
  await expect(
    page.getByText("Recovery codes copied", { exact: true }),
  ).toBeVisible();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(
    codes.join("\n"),
  );
  await page.getByRole("button", { name: "I have saved these" }).click();
  await page.getByRole("button", { name: "Replace recovery codes" }).click();
  const dialog = page.getByRole("dialog");
  await dialog
    .getByLabel("Current password", { exact: true })
    .fill("browser-test-password-123");
  await dialog
    .getByLabel("Authenticator or unused recovery code")
    .fill(codes[0]);
  await dialog.getByRole("button", { name: "Confirm", exact: true }).click();
  await expect(page.locator(".recovery-codes code")).toHaveCount(10);
  const replacement = await page
    .locator(".recovery-codes code")
    .allTextContents();
  expect(replacement[0]).not.toBe(codes[0]);
  await page.getByRole("button", { name: "I have saved these" }).click();
  await page.getByRole("button", { name: "Disable MFA" }).click();
  await dialog
    .getByLabel("Current password", { exact: true })
    .fill("browser-test-password-123");
  await dialog
    .getByLabel("Authenticator or unused recovery code")
    .fill(replacement[0]);
  await dialog.getByRole("button", { name: "Confirm", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Set up authenticator" }),
  ).toBeVisible();
  await page
    .getByLabel("Current password", { exact: true })
    .last()
    .fill("browser-test-password-123");
  await page
    .getByLabel("New password (12+ characters)")
    .fill("browser-new-password-456");
  await page.getByRole("button", { name: "Update password" }).click();
  await expect(
    page.getByRole("heading", { name: "Welcome to PulseGrid." }),
  ).toBeVisible();
});
