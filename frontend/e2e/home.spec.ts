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

// These two read data from the backend, so they fail if the API or the seed is missing.
test("shows a seeded psychologist from the backend", async ({ page }) => {
  await page.goto("/psikolog/elif-aydin");
  await expect(
    page.getByRole("heading", { level: 1, name: "Elif Aydın" }),
  ).toBeVisible();
  await expect(page.getByRole("heading", { name: "Hizmetler" })).toBeVisible();
});

test("reports an unknown psychologist", async ({ page }) => {
  await page.goto("/psikolog/no-such-psychologist");
  await expect(page.getByText("Psikolog profili bulunamadı.")).toBeVisible();
});
