import { expect, test } from "@playwright/test";

test("shows the Turkish landing page", async ({ page }) => {
  await page.goto("/");
  await expect(
    page.getByRole("heading", {
      name: "Psikoloji pratiğiniz için sade bir çalışma alanı",
    }),
  ).toBeVisible();
});

test("opens Turkish login and registration routes", async ({ page }) => {
  await page.goto("/giris");
  await expect(
    page.getByText("Giriş yap", { exact: true }).first(),
  ).toBeVisible();

  await page.goto("/kayit");
  await expect(
    page.getByText("Hesap oluştur", { exact: true }).first(),
  ).toBeVisible();
});

test("protects the own appointments route", async ({ page }) => {
  await page.goto("/randevularim");
  await expect(page).toHaveURL(/\/giris(?:\?|$)/, { timeout: 15_000 });
});

test("loads a public psychologist route", async ({ page }) => {
  await page.goto("/psikolog/elif-aydin");
  await expect(page.locator("body")).toBeVisible();
});
