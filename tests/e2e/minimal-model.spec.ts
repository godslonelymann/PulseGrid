import { operatorSession } from "./operator-session";
import { test, expect } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { resolve } from "node:path";

test("configuration history and archive/restore preserve service checks", async ({
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
        session.add(User(email="model.browser@example.com", password_hash=hasher.hash("model-test-password-123")))
    await engine.dispose()
asyncio.run(main())
`,
    ],
    { cwd: "../..", env: { ...process.env, PYTHONPATH: "apps/api" } },
  );
  await operatorSession(
    page,
    "model.browser@example.com",
    "model-test-password-123",
  );
  await page.getByRole("button", { name: "Add service", exact: true }).click();
  await page.getByLabel("Name (optional)").fill("Versioned service");
  await page
    .getByLabel("Website or API URL")
    .fill("http://127.0.0.1:18080/health");
  await page.getByRole("button", { name: "Start monitoring" }).click();
  await page.getByRole("button", { name: /^Versioned service http/ }).click();
  await expect(page).toHaveURL(/monitor=/);
  await expect(
    page.getByRole("heading", { name: "Versioned service", exact: true }),
  ).toBeVisible();
  const id = new URL(page.url()).searchParams.get("monitor")!;
  await expect
    .poll(
      async () =>
        (
          await (
            await page.request.get(`/api/v1/monitors/${id}/results`)
          ).json()
        ).length,
    )
    .toBeGreaterThan(0);
  const original = (
    await (await page.request.get(`/api/v1/monitors/${id}/results`)).json()
  )[0];
  await page.getByRole("button", { name: "Edit service", exact: true }).click();
  await page
    .getByLabel("Website or API URL")
    .fill("http://127.0.0.1:18080/other");
  await page.getByRole("button", { name: "Save changes" }).click();
  await page.getByText("More details", { exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Configuration history" }),
  ).toBeVisible();
  await expect(
    page.getByText("http://127.0.0.1:18080/health", {
      exact: true,
    }),
  ).toBeVisible();
  const versions = await (
    await page.request.get(`/api/v1/monitors/${id}/versions`)
  ).json();
  expect(
    versions.find(
      (version: { id: string }) => version.id === original.monitor_version_id,
    ).url,
  ).toBe("http://127.0.0.1:18080/health");
  const archive = page.getByRole("button", {
    name: "Archive service",
    exact: true,
  });
  const remove = page.getByRole("button", { name: "Delete", exact: true });
  await expect(archive).toHaveCSS("width", "200px");
  await expect(remove).toHaveCSS("width", "200px");
  await archive.click();
  await expect(
    page.getByRole("button", { name: "Restore service", exact: true }),
  ).toHaveCSS("width", "200px");
  expect(
    (await (await page.request.get("/api/v1/dashboard")).json()).monitors,
  ).toHaveLength(0);
  const retained = await (
    await page.request.get(`/api/v1/monitors/${id}/results`)
  ).json();
  expect(
    retained.some((check: { id: string }) => check.id === original.id),
  ).toBeTruthy();
  await page.getByRole("button", { name: "Back to dashboard" }).click();
  await page.getByRole("button", { name: "Archived", exact: true }).click();
  await page.getByRole("button", { name: /^Versioned service http/ }).click();
  await page
    .getByRole("button", { name: "Restore service", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Archive service", exact: true }),
  ).toBeVisible();
  expect(
    (await (await page.request.get("/api/v1/dashboard")).json()).monitors,
  ).toHaveLength(1);
  await page.screenshot({
    path: "../../verification/model-history-and-restore.png",
    fullPage: true,
  });
});
