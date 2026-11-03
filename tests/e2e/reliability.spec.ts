import { expect, test } from "@playwright/test";
import { operatorSession } from "./operator-session";

test("delivery failures are visible without opening advanced settings", async ({
  page,
}) => {
  await operatorSession(
    page,
    "browser@example.com",
    "browser-test-password-123",
  );
  await page.getByRole("button", { name: "Alerts", exact: true }).click();
  const status = page.getByRole("region", { name: "Delivery status" });
  await expect(status.getByText("No tests sent yet.")).toBeVisible();
  const csrf = (await (await page.request.get("/api/v1/auth/csrf")).json())
    .csrf;
  const headers = { Origin: new URL(page.url()).origin, "X-CSRF-Token": csrf };
  const response = await page.request.post("/api/v1/notification-channels", {
    headers,
    data: {
      name: "Rejecting webhook",
      kind: "webhook",
      destination: "http://127.0.0.1:18080/missing",
      enabled: true,
    },
  });
  expect(response.ok()).toBeTruthy();
  const channel = await response.json();
  const queued = await page.request.post(
    `/api/v1/notification-channels/${channel.id}/test`,
    { headers },
  );
  expect(queued.ok()).toBeTruthy();
  await page.reload();
  await expect(status.getByRole("status")).toContainText(
    "Failed · retry scheduled",
  );
  const failure = status
    .getByRole("row")
    .filter({ hasText: "Rejecting webhook" });
  await expect(
    failure.getByText("Notification provider unavailable or rejected request"),
  ).toBeVisible();
  await expect(failure.getByRole("button", { name: "Retry" })).toBeEnabled();
  await expect(
    page.getByRole("heading", { name: "Delivery history" }),
  ).toBeHidden();
});

test("long response time windows render the trend", async ({ page }) => {
  await operatorSession(
    page,
    "browser@example.com",
    "browser-test-password-123",
  );
  await page.getByRole("button", { name: "Add service", exact: true }).click();
  await page.getByLabel("Name (optional)").fill("Response time fixture");
  await page
    .getByLabel("Website or API URL")
    .fill("http://127.0.0.1:18080/health");
  await page.getByRole("button", { name: "Start monitoring" }).click();
  await page
    .getByRole("button", { name: /^Response time fixture http/ })
    .click();
  const window = page.getByLabel("Metric window");
  const chart = page
    .locator(".chart-panel")
    .filter({
      has: page.getByRole("heading", { name: "Response time", exact: true }),
    });
  for (const [hours, bucket] of [
    ["168", "1 hr"],
    ["720", "6 hr"],
  ]) {
    await window.selectOption(hours);
    await expect(
      chart.getByText(
        `Median time to response headers for successful checks · per ${bucket}`,
      ),
    ).toBeVisible();
    await expect(chart.locator(".recharts-area-dot").first()).toBeVisible();
  }
});
