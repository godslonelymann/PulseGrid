import { expect, test } from "@playwright/test";

test("public landing works without the API and its CTAs open the existing account screens", async ({
  page,
}) => {
  const requests: string[] = [];
  await page.route("**/api/v1/**", (route) => {
    const path = new URL(route.request().url()).pathname;
    requests.push(path);
    if (path.endsWith("/auth/csrf")) {
      return route.fulfill({
        json: { csrf: "test-csrf" },
        headers: { "set-cookie": "pg_csrf=test-csrf; Path=/" },
      });
    }
    if (path.endsWith("/auth/google/config")) {
      return route.fulfill({ json: { enabled: false } });
    }
    return route.fulfill({ status: 401, json: { detail: "Sign in required" } });
  });

  await page.clock.install();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  const heading = page.getByRole("heading", {
    name: "Know when it goes down.",
  });
  await expect(heading).toBeVisible();

  const demo = page.getByRole("region", {
    name: "Sample incident",
    exact: true,
  });
  const status = demo.getByRole("status");
  await expect(status).toHaveText("Recovered");
  await expect(demo.getByText("HTTP 200")).toBeVisible();
  await expect(demo.getByText("Recovery alert queued")).toBeVisible();
  const now = await page.evaluate(() => Date.now());
  await page.clock.pauseAt(new Date(now + 1000));
  await demo.getByRole("button", { name: "Replay incident" }).press("Enter");
  await expect(status).toHaveText("Healthy");
  await page.clock.runFor(550);
  await expect(demo.getByText("First failure", { exact: true })).toBeVisible();
  await expect(demo.getByText("HTTP 503")).toBeVisible();
  await demo.getByRole("button", { name: "Pause incident" }).press("Space");
  await page.clock.runFor(800);
  await expect(status).toHaveText("Healthy");
  await demo.getByRole("button", { name: "Resume incident" }).press("Enter");
  await page.clock.runFor(600);
  await expect(status).toHaveText("Outage");
  await expect(
    demo.getByText("Incident opened · outage alert queued"),
  ).toBeVisible();
  await page.clock.runFor(900);
  await expect(demo.getByText("HTTP 200")).toBeVisible();
  await expect(
    demo.getByText("First success · incident still open"),
  ).toBeVisible();
  await expect(status).toHaveText("Outage");
  await page.clock.runFor(700);
  await expect(status).toHaveText("Recovered");
  await page.clock.runFor(700);
  await expect(
    demo.getByRole("button", { name: "Replay incident" }),
  ).toBeVisible();
  await demo
    .getByRole("button", { name: "Outage", exact: true })
    .press("Enter");
  await expect(status).toHaveText("Outage");
  await page.clock.runFor(4000);
  await expect(status).toHaveText("Outage");
  await demo
    .getByRole("button", { name: "Recovered", exact: true })
    .press("Space");
  await expect(status).toHaveText("Recovered");
  expect(requests).toEqual([]);
  await page.clock.resume();

  await page.getByRole("link", { name: "Start monitoring" }).click();
  await expect(
    page.getByRole("heading", { name: "Create your account" }),
  ).toBeVisible();
  await page.goBack();
  await page.getByRole("banner").getByRole("link", { name: "Get Started" }).click();
  await expect(
    page.getByRole("heading", { name: "Welcome to PulseGrid." }),
  ).toBeVisible();

  await page
    .context()
    .addCookies([
      { name: "pg_refresh", value: "expired", url: new URL(page.url()).origin },
    ]);
  await page.goto("/");
  await expect(heading).toBeVisible();
});

test("reduced motion retains instant keyboard step selection", async ({
  page,
}) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/");
  const demo = page.getByRole("region", {
    name: "Sample incident",
    exact: true,
  });
  for (const label of ["Healthy", "Outage", "Recovered"]) {
    await demo.getByRole("button", { name: label, exact: true }).press("Enter");
    await expect(demo.getByRole("status")).toHaveText(label);
  }
  await demo.getByRole("button", { name: "Replay incident" }).press("Enter");
  await expect(demo.getByRole("status")).toHaveText("Recovered");
  const scrubber = page.getByRole("slider", { name: "Select a check" });
  await scrubber.press("Home");
  await expect(scrubber).toHaveAttribute(
    "aria-valuetext",
    /14:00:00, HTTP 200/,
  );
});

for (const width of [1440, 390, 320]) {
  test(`landing fits at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: width === 1440 ? 900 : 844 });
    await page.goto("/");
    const demo = page.getByRole("region", {
      name: "Sample incident",
      exact: true,
    });
    await expect(
      demo.getByRole("button", { name: "Recovered", exact: true }),
    ).toBeInViewport();
    await expect(
      page.getByRole("link", { name: "Start monitoring" }).last(),
    ).toBeInViewport();
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth),
    ).toBe(width);
  });
}
