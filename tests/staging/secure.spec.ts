import { operatorSession } from "../e2e/operator-session";
import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
const password = readFileSync("../../.env.staging", "utf8")
  .split("\n")
  .find((x) => x.startsWith("STAGING_TEST_PASSWORD="))!
  .split("=")[1];
test("native production services: Caddy HTTPS, Secure cookies, protected monitor and live TLS probe", async ({
  page,
  context,
}) => {
  const monitorName = `Public HTTPS example ${Date.now()}`;
  await operatorSession(page, "staging@example.com", password);
  await expect(page.getByRole("heading", { name: "Dashboard." })).toBeVisible();
  const cookies = await context.cookies();
  for (const name of ["pg_access", "pg_refresh"]) {
    const cookie = cookies.find((c) => c.name === name)!;
    expect(cookie.secure).toBeTruthy();
    expect(cookie.httpOnly).toBeTruthy();
    expect(cookie.sameSite).toBe("Strict");
    expect(cookie.domain).toBe("localhost");
  }
  const csrf = cookies.find((c) => c.name === "pg_csrf")!.value;
  const rejected = await page.request.post("/api/v1/monitors", {
    headers: { Origin: "https://evil.example", "X-CSRF-Token": csrf },
    data: { url: "https://example.com/", name: "forbidden" },
  });
  expect(rejected.status()).toBe(403);
  await page.getByRole("button", { name: "Add service", exact: true }).click();
  await page.getByLabel("Name (optional)").fill(monitorName);
  await page.getByLabel("Website or API URL").fill("https://example.com/");
  await page.getByRole("button", { name: "Start monitoring" }).click();
  const monitor = page.getByRole("button", {
    name: new RegExp(`^${monitorName} https`),
  });
  await expect(monitor).toBeVisible();
  await monitor.click();
  await expect(page.locator(".page-heading .badge.up")).toBeVisible({
    timeout: 40000,
  });
  await page.screenshot({
    path: "../../verification/staging-https.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "Alerts", exact: true }).click();
  await page.getByText("Advanced alert settings", { exact: true }).click();
  await page.getByRole("button", { name: "Add channel" }).click();
  await page
    .getByLabel("Name", { exact: true })
    .fill("Encrypted restore fixture");
  await page.getByLabel("Type", { exact: true }).selectOption("email");
  await expect(page.getByLabel("Account email", { exact: true })).toHaveValue(
    "staging@example.com",
  );
  await page.getByRole("button", { name: "Save channel" }).click();
  await expect(
    page.getByRole("heading", { name: "Encrypted restore fixture" }).last(),
  ).toBeVisible();
  await page.getByRole("button", { name: "Settings" }).click();
  await page.getByLabel("Current password").first().fill(password);
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
  await page.getByRole("button", { name: "I have saved these" }).click();
});
