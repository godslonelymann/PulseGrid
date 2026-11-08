import { test, expect, type Page } from "@playwright/test";

async function signedOut(page: Page, enabled = true) {
  await page.route("**/api/v1/me", (route) =>
    route.fulfill({ status: 401, json: { detail: "Sign in required" } }),
  );
  await page.route("**/api/v1/auth/refresh", (route) =>
    route.fulfill({ status: 401, json: { detail: "No session" } }),
  );
  await page.route("**/api/v1/auth/google/config", (route) =>
    route.fulfill({ json: { enabled } }),
  );
}

test("Google button starts authorization and fits desktop and mobile", async ({
  page,
}) => {
  await signedOut(page);
  let started = false;
  await page.route("**/api/v1/auth/google/start", (route) => {
    started = true;
    expect(route.request().method()).toBe("POST");
    expect(route.request().headers()["x-csrf-token"]).toBeTruthy();
    return route.fulfill({
      json: {
        authorization_url:
          "https://accounts.google.com/o/oauth2/v2/auth?test=1",
      },
    });
  });
  await page.route("https://accounts.google.com/**", (route) =>
    route.fulfill({ contentType: "text/html", body: "Google consent" }),
  );
  await page.goto("/?auth=login");
  await expect(
    page.getByRole("button", { name: "Continue with Google" }),
  ).toBeEnabled();
  await expect(page.getByLabel("Email", { exact: true })).toBeVisible();
  await expect(page.getByLabel("Password", { exact: true })).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Log in", exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: "../../verification/google-signup-desktop.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(
    page.getByRole("button", { name: "Continue with Google" }),
  ).toBeVisible();
  await expect(page.getByLabel("Email", { exact: true })).toBeVisible();
  await expect(page.getByLabel("Password", { exact: true })).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBeTruthy();
  await page.screenshot({
    path: "../../verification/google-signup-mobile.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "Continue with Google" }).click();
  await expect(page).toHaveURL(/accounts\.google\.com/);
  expect(started).toBeTruthy();
});

test("Google button is disabled until credentials are configured", async ({
  page,
}) => {
  await signedOut(page, false);
  await page.goto("/?auth=login");
  await expect(
    page.getByRole("button", { name: "Continue with Google" }),
  ).toBeDisabled();
  await expect(
    page.getByText("Google sign-in is not configured yet."),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Log in", exact: true }),
  ).toBeVisible();
});

test("Google callback errors and MFA challenges are displayed and removed from the URL", async ({
  page,
}) => {
  await signedOut(page);
  await page.goto("/#google_error=email_exists");
  await expect(page.getByRole("main").getByRole("alert")).toContainText(
    "Contact your administrator",
  );
  await expect(page).toHaveURL("/?auth=login");
  await page.goto("about:blank");
  await page.goto("/#google_challenge=one-time-google-challenge");
  await expect(
    page.getByRole("heading", { name: "Verify your sign-in" }),
  ).toBeVisible();
  await expect(page).toHaveURL("/?auth=login");
  await page.route("**/api/v1/auth/verify", (route) => {
    expect(route.request().postDataJSON()).toEqual({
      challenge: "one-time-google-challenge",
      code: "123456",
    });
    return route.fulfill({
      status: 401,
      json: { detail: "Invalid or already used factor" },
    });
  });
  await page.getByLabel("Authentication or recovery code").fill("123456");
  await page.getByRole("button", { name: "Verify and sign in" }).click();
  await expect(page.getByRole("main").getByRole("alert")).toContainText(
    "Invalid or already used factor",
  );
});

test("Google account settings distinguish current and global sign-out", async ({
  page,
}) => {
  let signedOut = false;
  await page.route("**/api/v1/**", (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/auth/logout-all")) {
      expect(route.request().method()).toBe("POST");
      signedOut = true;
      return route.fulfill({ json: { ok: true } });
    }
    if (signedOut && (path.endsWith("/me") || path.endsWith("/auth/refresh"))) {
      return route.fulfill({
        status: 401,
        json: { detail: "Sign in required" },
      });
    }
    const json = path.endsWith("/me")
      ? {
          id: "google-user",
          email: "google.user@gmail.com",
          mfa_enabled: false,
          password_enabled: false,
          google_connected: true,
          session_id: "session",
        }
      : path.endsWith("/dashboard")
        ? { monitors: [], counts: {} }
        : path.endsWith("/worker-status")
          ? { workers: [], outbox_pending: 0, outbox_failed: 0 }
          : path.endsWith("/notification-settings")
            ? {
                email_available: true,
                email_enabled: true,
                email_channel_id: "email",
              }
            : [];
    return route.fulfill({ json });
  });
  await page.route("**/api/v1/sessions", (route) =>
    route.fulfill({
      json: [
        {
          id: "other-session",
          label: "Firefox",
          created_at: "2026-01-01T00:00:00Z",
          last_activity: "2026-01-01T00:00:00Z",
          current: false,
        },
      ],
    }),
  );
  await page.goto("/?view=settings#google_success=1");
  await expect(
    page.getByRole("heading", { name: "Account & security" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Google account", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("link", { name: "Manage Google security" }),
  ).toHaveCount(0);
  await expect(page.getByLabel("Current password")).toHaveCount(0);
  await expect(
    page.getByRole("switch", { name: "Email me about outages and recoveries" }),
  ).toBeChecked();
  await expect(page.getByText("This browser", { exact: true })).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Sign out of this browser", exact: true }),
  ).toBeVisible();
  await expect(page).toHaveURL("/?view=settings");
  await page.setViewportSize({ width: 320, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
  const signOutEverywhere = page.getByRole("button", {
    name: "Sign out everywhere",
    exact: true,
  });
  const currentAction = page.getByRole("button", {
    name: "Sign out of this browser",
    exact: true,
  });
  const otherAction = page.getByRole("button", {
    name: "End this session",
    exact: true,
  });
  await expect(signOutEverywhere).toHaveCSS("width", "200px");
  await expect(currentAction).toHaveCSS("width", "200px");
  await expect(otherAction).toHaveCSS("width", "200px");
  const right = await signOutEverywhere.evaluate(
    (button) => button.getBoundingClientRect().right,
  );
  expect(
    await currentAction.evaluate(
      (button) => button.getBoundingClientRect().right,
    ),
  ).toBe(right);
  expect(
    await otherAction.evaluate(
      (button) => button.getBoundingClientRect().right,
    ),
  ).toBe(right);
  await signOutEverywhere.click();
  const dialog = page.getByRole("dialog", { name: "Sign out everywhere?" });
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(signOutEverywhere).toBeFocused();
  expect(signedOut).toBe(false);
  await signOutEverywhere.click();
  await dialog
    .getByRole("button", { name: "Sign out everywhere", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Welcome to PulseGrid." }),
  ).toBeVisible();
  expect(signedOut).toBe(true);
});
