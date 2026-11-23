"use client";

import { useParams, useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { useQuery } from "@tanstack/react-query";
import { Suspense, useState } from "react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { ApiError } from "@/lib/api";
import { authErrorMessage } from "@/lib/error-messages";
import {
  bookAppointment,
  getPublicProfile,
  getPublicSlots,
  type OfferedService,
} from "@/lib/scheduling";
import { useSession } from "@/providers/session-provider";

function dateInputValue(date: Date) {
  const year = date.getFullYear();
  const month = `${date.getMonth() + 1}`.padStart(2, "0");
  const day = `${date.getDate()}`.padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function PublicPsychologistContent() {
  const t = useTranslations("Scheduling");
  const params = useParams<{ slug: string }>();
  const router = useRouter();
  const { user } = useSession();
  const slug = params.slug;
  const [selectedService, setSelectedService] = useState<string>("");
  const [from, setFrom] = useState(() => dateInputValue(new Date()));
  const [to, setTo] = useState(() => {
    const date = new Date();
    date.setDate(date.getDate() + 14);
    return dateInputValue(date);
  });
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");

  const profileQuery = useQuery({
    queryKey: ["public-profile", slug],
    queryFn: () => getPublicProfile(slug),
  });

  const profile = profileQuery.data;
  const activeService = profile?.services.find(
    (service) => service.id === selectedService,
  );
  const slotsQuery = useQuery({
    queryKey: ["public-slots", slug, selectedService, from, to],
    queryFn: () => getPublicSlots(slug, selectedService, from, to),
    enabled: Boolean(selectedService && from && to),
  });

  async function book(slot: string) {
    setError("");
    setSuccess("");
    if (!user) {
      router.push("/giris");
      return;
    }
    if (user.role !== "client") {
      setError("Randevu yalnızca danışan oturumuyla alınabilir.");
      return;
    }
    try {
      await bookAppointment(slug, selectedService, slot);
      setSuccess(t("bookSuccess"));
      await slotsQuery.refetch();
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? authErrorMessage(caught.code)
          : "İstek tamamlanamadı.",
      );
    }
  }

  if (profileQuery.isLoading) {
    return (
      <main className="mx-auto max-w-5xl px-4 py-10">
        {t("profileLoading")}
      </main>
    );
  }
  if (profileQuery.isError || !profile) {
    return (
      <main className="mx-auto max-w-5xl px-4 py-10">
        <Alert variant="destructive">
          <AlertDescription>{t("profileNotFound")}</AlertDescription>
        </Alert>
      </main>
    );
  }

  return (
    <main className="mx-auto max-w-5xl px-4 py-10">
      {error && (
        <Alert variant="destructive" className="mb-4">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      {success && (
        <Alert className="mb-4">
          <AlertDescription>{success}</AlertDescription>
        </Alert>
      )}
      <div className="grid gap-6 lg:grid-cols-[1fr_340px]">
        <div className="space-y-6">
          <section>
            <p className="text-sm font-medium text-primary">
              {profile.title ?? "Psikolog"}
            </p>
            <h1 className="mt-1 text-4xl font-semibold">
              {profile.first_name} {profile.last_name}
            </h1>
            {profile.bio && (
              <p className="mt-4 max-w-2xl leading-7 text-muted-foreground">
                {profile.bio}
              </p>
            )}
          </section>

          <section>
            <h2 className="mb-3 text-xl font-semibold">{t("services")}</h2>
            <div className="grid gap-3 sm:grid-cols-2">
              {profile.services.map((service: OfferedService) => (
                <Card
                  key={service.id}
                  className={
                    selectedService === service.id
                      ? "border-primary"
                      : "cursor-pointer"
                  }
                  onClick={() => setSelectedService(service.id)}
                >
                  <CardHeader>
                    <CardTitle className="text-base">{service.name}</CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-2 text-sm">
                    {service.description && (
                      <p className="text-muted-foreground">
                        {service.description}
                      </p>
                    )}
                    <div className="flex items-center justify-between">
                      <Badge variant="secondary">
                        {service.duration_minutes} dk
                      </Badge>
                      <span className="font-medium">
                        {new Intl.NumberFormat("tr-TR", {
                          style: "currency",
                          currency: service.currency,
                        }).format(Number(service.price_amount))}
                      </span>
                    </div>
                  </CardContent>
                </Card>
              ))}
            </div>
          </section>
        </div>

        <aside>
          <Card className="sticky top-6">
            <CardHeader>
              <CardTitle>{t("chooseDate")}</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              {activeService && (
                <p className="text-sm text-muted-foreground">
                  {activeService.name} · {activeService.duration_minutes} dk
                </p>
              )}
              <div className="grid grid-cols-2 gap-3">
                <label className="text-sm">
                  {t("from")}
                  <Input
                    className="mt-1"
                    type="date"
                    value={from}
                    onChange={(event) => setFrom(event.target.value)}
                  />
                </label>
                <label className="text-sm">
                  {t("to")}
                  <Input
                    className="mt-1"
                    type="date"
                    value={to}
                    onChange={(event) => setTo(event.target.value)}
                  />
                </label>
              </div>

              {!selectedService && (
                <p className="text-sm text-muted-foreground">
                  {t("chooseService")}
                </p>
              )}
              {selectedService && slotsQuery.isLoading && (
                <p className="text-sm text-muted-foreground">{t("loading")}</p>
              )}
              {selectedService &&
                slotsQuery.data &&
                slotsQuery.data.slots.length === 0 && (
                  <p className="text-sm text-muted-foreground">
                    {t("noSlots")}
                  </p>
                )}
              {slotsQuery.data &&
                slotsQuery.data.slots.map((slot) => (
                  <Button
                    key={slot.starts_at}
                    variant="outline"
                    className="w-full"
                    onClick={() => book(slot.starts_at)}
                  >
                    {new Intl.DateTimeFormat("tr-TR", {
                      weekday: "short",
                      day: "numeric",
                      month: "short",
                      hour: "2-digit",
                      minute: "2-digit",
                      timeZone: slotsQuery.data.timezone,
                    }).format(new Date(slot.starts_at))}
                  </Button>
                ))}
            </CardContent>
          </Card>
        </aside>
      </div>
    </main>
  );
}

export default function PublicPsychologistPage() {
  return (
    <Suspense
      fallback={
        <main className="mx-auto max-w-5xl px-4 py-10">
          Profil yükleniyor...
        </main>
      }
    >
      <PublicPsychologistContent />
    </Suspense>
  );
}
