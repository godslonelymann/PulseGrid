import { test, expect, type Page } from "@playwright/test";

async function signedOut(page: Page) {
  await page.route("**/api/v1/me", (route) =>
    route.fulfill({ status: 401, json: { detail: "Sign in required" } }),
  );
  await page.route("**/api/v1/auth/refresh", (route) =>
    route.fulfill({ status: 401, json: { detail: "No session" } }),
  );
  await page.route("**/api/v1/auth/google/config", (route) =>
    route.fulfill({ json: { enabled: false } }),
  );
}

test("login offers only Google and does not poll while signed out", async ({
  page,
}) => {
  await signedOut(page);
  let checks = 0;
  page.on("request", (request) => {
    if (request.url().endsWith("/api/v1/me")) checks++;
  });
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Welcome to PulseGrid." }),
  ).toBeVisible();
  await expect(page.getByLabel("Email address")).toHaveCount(0);
  await expect(page.getByLabel("Password", { exact: true })).toHaveCount(0);
  await expect(page.getByText("or sign in with email")).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Sign in", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Continue with Google" }),
  ).toBeVisible();
  const initial = checks;
  await page.waitForTimeout(11000);
  expect(checks).toBe(initial);
});

test("session checks retain settings during interruption, recover, renew, and reject revoked sessions", async ({
  page,
}) => {
  let mode = "healthy";
  let checks = 0;
  let renewals = 0;
  await page.route("**/api/v1/me", (route) => {
    checks++;
    return route.fulfill(
      mode === "unavailable"
        ? { status: 503, json: { detail: "API temporarily unavailable" } }
        : mode === "expired" || mode === "revoked"
          ? { status: 401, json: { detail: "Session expired" } }
          : {
              json: {
                id: "user",
                email: "owner@example.com",
                mfa_enabled: false,
                password_enabled: true,
                google_connected: false,
                session_id: "session",
              },
            },
    );
  });
  await page.route("**/api/v1/auth/refresh", (route) => {
    renewals++;
    if (mode === "expired") {
      mode = "healthy";
      return route.fulfill({ json: { ok: true } });
    }
    return route.fulfill({ status: 401, json: { detail: "Session revoked" } });
  });
  await page.route("**/api/v1/sessions", (route) =>
    route.fulfill({ json: [] }),
  );
  await page.route("**/api/v1/dashboard", (route) =>
    route.fulfill({ json: { monitors: [], counts: {} } }),
  );
  await page.route("**/api/v1/worker-status", (route) =>
    route.fulfill({ json: { workers: [] } }),
  );
  await page.goto("/?view=settings");
  const heading = page.getByRole("heading", { name: "Account & security" });
  await expect(heading).toBeVisible();
  const password = page.getByLabel("Current password", { exact: true }).first();
  await password.fill("preserve-my-input");
  mode = "unavailable";
  await expect(page.getByRole("alert").first()).toContainText(
    "Connection interrupted",
    { timeout: 20000 },
  );
  await expect(heading).toBeVisible();
  await expect(password).toHaveValue("preserve-my-input");
  mode = "healthy";
  await page.getByRole("button", { name: "Try again" }).click();
  await expect(
    page.getByText("Connection interrupted", { exact: false }),
  ).toHaveCount(0);
  await expect(password).toHaveValue("preserve-my-input");
  mode = "expired";
  await expect.poll(() => renewals, { timeout: 20000 }).toBeGreaterThan(0);
  await expect(heading).toBeVisible();
  mode = "revoked";
  await expect(
    page.getByRole("heading", { name: "Welcome to PulseGrid." }),
  ).toBeVisible({ timeout: 20000 });
  const stoppedAt = checks;
  await page.waitForTimeout(11000);
  expect(checks).toBe(stoppedAt);
});
