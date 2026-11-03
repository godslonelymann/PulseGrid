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

test("login offers email and Google without polling while signed out", async ({
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
  await expect(page.getByLabel("Email", { exact: true })).toBeVisible();
  await expect(page.getByLabel("Password", { exact: true })).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Log in", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Continue with Google" }),
  ).toBeVisible();
  const initial = checks;
  await page.waitForTimeout(11000);
  expect(checks).toBe(initial);
});

test("signup requests a link, resends it, and handles a used link opened in the same tab", async ({
  page,
}) => {
  await signedOut(page);
  const email = "new-user@example.com";
  let requests = 0;
  await page.route("**/api/v1/auth/email/signup", (route) => {
    expect(route.request().postDataJSON()).toEqual({ email });
    requests++;
    return route.fulfill({
      status: 202,
      json: { detail: "If eligible, you’ll receive a confirmation link." },
    });
  });
  await page.route("**/api/v1/auth/email/confirm", (route) =>
    route.fulfill({
      status: 400,
      json: { detail: "This link was already used." },
    }),
  );

  await page.goto("/");
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page.getByLabel("Password", { exact: true })).toHaveCount(0);
  await page.getByLabel("Email", { exact: true }).fill(email);
  await page.getByRole("button", { name: "Send confirmation link" }).click();
  await expect(
    page.getByRole("heading", { name: "Check your inbox" }),
  ).toBeFocused();
  await page.getByRole("button", { name: "Resend confirmation email" }).click();
  await expect(page.getByRole("status")).toContainText(
    "Request submitted again",
  );
  expect(requests).toBe(2);

  await page.goto("/auth/email");
  await expect(
    page.getByText("This link is invalid.", { exact: false }),
  ).toBeVisible();
  await page.goto("/auth/email#flow=confirm&token=fixture-token");
  await page
    .getByLabel("New password (12+ characters)")
    .fill("long-password-123");
  await page.getByRole("button", { name: "Create account" }).click();
  await page.getByRole("link", { name: "Request a new link" }).click();
  await expect(
    page.getByRole("heading", { name: "Create your account" }),
  ).toBeVisible();
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
  await page.route("**/api/v1/notification-settings", (route) =>
    route.fulfill({
      json: {
        email_available: false,
        email_enabled: false,
        email_channel_id: null,
      },
    }),
  );
  await page.route("**/api/v1/deliveries?*", (route) =>
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
