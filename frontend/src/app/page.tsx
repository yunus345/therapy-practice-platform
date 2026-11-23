import { getTranslations } from "next-intl/server";
import Link from "next/link";

export default async function Home() {
  const t = await getTranslations("Home");

  return (
    <main className="flex min-h-screen items-center justify-center px-6">
      <section className="mx-auto max-w-2xl text-center">
        <p className="mb-3 text-sm font-semibold uppercase tracking-[0.2em] text-primary">
          {t("eyebrow")}
        </p>
        <h1 className="text-4xl font-semibold tracking-tight sm:text-5xl">
          {t("title")}
        </h1>
        <p className="mt-5 text-lg leading-8 text-muted-foreground">
          {t("description")}
        </p>
        <div className="mt-8 flex justify-center gap-3">
          <Link
            className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground"
            href="/giris"
          >
            Giriş yap
          </Link>
          <Link
            className="rounded-md border border-border px-4 py-2 text-sm font-medium"
            href="/kayit"
          >
            Hesap oluştur
          </Link>
        </div>
      </section>
    </main>
  );
}
