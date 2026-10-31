import { expect, test } from "@playwright/test";

test("create a scenario from a template, run an experiment and read the fairness results", async ({ page, request }) => {
  const health = await request.get("/health");
  expect(health.ok()).toBeTruthy();

  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Scenarios", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Create from template" }).first().click();
  await expect(page.getByRole("button", { name: /Save new version/ })).toBeDisabled(); // unchanged => nothing to save
  await expect(page.getByText("Valid", { exact: true })).toBeVisible();

  await page.getByRole("link", { name: "Run an experiment" }).click();
  await page.getByLabel(/Base seed/).fill("20260101");
  await page.getByRole("button", { name: /^Run \d polic/ }).click();

  await expect(page.getByRole("tab", { name: "Overview" })).toBeVisible({ timeout: 90_000 });
  await expect(page.getByText("Headline metrics")).toBeVisible();

  await page.getByRole("tab", { name: "Fairness" }).click();
  await expect(page.getByText(/No policy is claimed to guarantee an aggregate constraint/)).toBeVisible();

  await page.getByRole("tab", { name: "Policy comparison" }).click();
  await expect(page.getByText(/equivalent workloads/i)).toBeVisible();

  await page.getByRole("tab", { name: "Reproducibility" }).click();
  await expect(page.getByText("20260101", { exact: true })).toBeVisible();
  const href = await page.getByRole("link", { name: /Manifest/ }).getAttribute("href");
  const manifest = await request.get(href!);
  expect(manifest.ok()).toBeTruthy();
  expect((await manifest.json()).base_seed).toBe(20260101);
});
