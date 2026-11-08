import type { Page } from "@playwright/test";
import assert from "node:assert/strict";

// Seed legacy operator sessions through the API; the browser login offers Google only.
export async function operatorSession(
  page: Page,
  email: string,
  password: string,
) {
  await page.goto("/?auth=login");
  await page
    .getByRole("heading", { name: "Welcome to PulseGrid." })
    .waitFor({ state: "visible" });
  const origin = new URL(page.url()).origin;
  const csrf = await page.request.get("/api/v1/auth/csrf");
  assert(csrf.ok(), "Operator CSRF initialization failed");
  const token = (await csrf.json()).csrf;
  const login = await page.request.post("/api/v1/auth/login", {
    headers: { Origin: origin, "X-CSRF-Token": token },
    data: { email, password },
  });
  assert(login.ok(), "Operator fixture login failed");
  const result = await login.json();
  await page.goto("about:blank");
  await page.goto(
    result.mfa_required
      ? `/#google_challenge=${encodeURIComponent(result.challenge)}`
      : "/",
  );
}
