"use client";

import { useTranslations } from "next-intl";
import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { Suspense, useEffect, useState } from "react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ApiError } from "@/lib/api";
import { errorMessage } from "@/lib/error-messages";
import {
  cancelOwnAppointment,
  getPublicProfile,
  getPublicSlots,
  listOwnAppointments,
  rescheduleOwnAppointment,
  type ClientAppointment,
} from "@/lib/scheduling";
import { useSession } from "@/providers/session-provider";

function dateInputValue(date: Date) {
  const year = date.getFullYear();
  const month = `${date.getMonth() + 1}`.padStart(2, "0");
  const day = `${date.getDate()}`.padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function statusLabel(status: string) {
  const labels: Record<string, string> = {
    requested: "Talep edildi",
    confirmed: "Onaylandı",
    completed: "Tamamlandı",
    cancelled: "İptal edildi",
    no_show: "Gelmedi",
  };
  return labels[status] ?? status;
}

function OwnAppointmentsContent() {
  const t = useTranslations("Scheduling");
  const router = useRouter();
  const { user, loading } = useSession();
  const [filter, setFilter] = useState<"all" | "upcoming" | "past">("upcoming");
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [rescheduling, setRescheduling] = useState<ClientAppointment | null>(
    null,
  );
  const [from, setFrom] = useState(() => dateInputValue(new Date()));
  const [to, setTo] = useState(() => {
    const date = new Date();
    date.setDate(date.getDate() + 14);
    return dateInputValue(date);
  });

  useEffect(() => {
    if (!loading && !user) router.replace("/giris");
  }, [loading, user, router]);

  const appointmentsQuery = useQuery({
    queryKey: ["own-appointments", filter],
    queryFn: () => listOwnAppointments(filter),
    enabled: Boolean(user?.role === "client"),
  });

  const profileQuery = useQuery({
    queryKey: ["reschedule-profile", rescheduling?.psychologist_slug],
    queryFn: () => getPublicProfile(rescheduling!.psychologist_slug),
    enabled: Boolean(rescheduling),
  });

  const rescheduleService =
    profileQuery.data?.services.find(
      (service) => service.name === rescheduling?.service_name,
    ) ?? null;

  const slotsQuery = useQuery({
    queryKey: [
      "reschedule-slots",
      rescheduling?.psychologist_slug,
      rescheduleService?.id,
      from,
      to,
    ],
    queryFn: () =>
      getPublicSlots(
        rescheduling!.psychologist_slug,
        rescheduleService!.id,
        from,
        to,
      ),
    enabled: Boolean(rescheduling && rescheduleService && from && to),
  });

  async function cancel(appointment: ClientAppointment) {
    if (!window.confirm(t("cancelConfirm"))) return;
    setError("");
    setSuccess("");
    try {
      await cancelOwnAppointment(appointment.id);
      setSuccess(t("cancelSuccess"));
      await appointmentsQuery.refetch();
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? errorMessage(caught.code)
          : "İstek tamamlanamadı.",
      );
    }
  }

  async function reschedule(appointmentId: string, startsAt: string) {
    setError("");
    setSuccess("");
    try {
      await rescheduleOwnAppointment(appointmentId, startsAt);
      setRescheduling(null);
      setSuccess(t("rescheduleSuccess"));
      await appointmentsQuery.refetch();
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? errorMessage(caught.code)
          : "İstek tamamlanamadı.",
      );
    }
  }

  if (loading || !user) return null;
  if (user.role !== "client") {
    return (
      <main className="mx-auto max-w-4xl px-4 py-10">
        <Alert variant="destructive">
          <AlertDescription>
            Bu ekran yalnızca danışan oturumları içindir.
          </AlertDescription>
        </Alert>
      </main>
    );
  }

  return (
    <main className="mx-auto max-w-4xl px-4 py-10">
      <h1 className="mb-6 text-3xl font-semibold">{t("appointments")}</h1>
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
      <Tabs
        value={filter}
        onValueChange={(value) =>
          setFilter(value as "all" | "upcoming" | "past")
        }
      >
        <TabsList>
          <TabsTrigger value="upcoming">{t("upcoming")}</TabsTrigger>
          <TabsTrigger value="past">{t("past")}</TabsTrigger>
          <TabsTrigger value="all">{t("all")}</TabsTrigger>
        </TabsList>
      </Tabs>

      {appointmentsQuery.isLoading && (
        <p className="mt-6 text-muted-foreground">{t("loading")}</p>
      )}
      {appointmentsQuery.data?.items.length === 0 && (
        <p className="mt-6 text-muted-foreground">{t("noAppointments")}</p>
      )}
      <div className="mt-6 space-y-4">
        {appointmentsQuery.data?.items.map((appointment) => (
          <Card key={appointment.id}>
            <CardHeader>
              <div className="flex items-start justify-between gap-4">
                <div>
                  <CardTitle>{appointment.psychologist_name}</CardTitle>
                  <p className="mt-1 text-sm text-muted-foreground">
                    {appointment.service_name}
                  </p>
                </div>
                <Badge
                  variant={
                    appointment.status === "cancelled" ? "secondary" : "default"
                  }
                >
                  {statusLabel(appointment.status)}
                </Badge>
              </div>
            </CardHeader>
            <CardContent>
              <p className="font-medium">
                {new Intl.DateTimeFormat("tr-TR", {
                  weekday: "long",
                  day: "numeric",
                  month: "long",
                  year: "numeric",
                  hour: "2-digit",
                  minute: "2-digit",
                  timeZone: appointment.timezone,
                }).format(new Date(appointment.starts_at))}
              </p>
              {appointment.status !== "cancelled" &&
                appointment.status !== "completed" &&
                appointment.status !== "no_show" && (
                  <div className="mt-4 flex gap-2">
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => setRescheduling(appointment)}
                    >
                      {t("reschedule")}
                    </Button>
                    <Button
                      variant="destructive"
                      size="sm"
                      onClick={() => cancel(appointment)}
                    >
                      {t("cancel")}
                    </Button>
                  </div>
                )}

              {rescheduling?.id === appointment.id && (
                <div className="mt-5 rounded-md border p-4">
                  <p className="mb-3 text-sm font-medium">
                    {t("rescheduleSelect")}
                  </p>
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
                  {slotsQuery.isLoading && (
                    <p className="mt-3 text-sm text-muted-foreground">
                      {t("loading")}
                    </p>
                  )}
                  <div className="mt-3 grid gap-2 sm:grid-cols-2">
                    {slotsQuery.data?.slots.map((slot) => (
                      <Button
                        key={slot.starts_at}
                        variant="outline"
                        onClick={() =>
                          reschedule(appointment.id, slot.starts_at)
                        }
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
                  </div>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="mt-3"
                    onClick={() => setRescheduling(null)}
                  >
                    Vazgeç
                  </Button>
                </div>
              )}
            </CardContent>
          </Card>
        ))}
      </div>
    </main>
  );
}

export default function OwnAppointmentsPage() {
  return (
    <Suspense
      fallback={
        <main className="mx-auto max-w-4xl px-4 py-10">
          Randevularım yükleniyor...
        </main>
      }
    >
      <OwnAppointmentsContent />
    </Suspense>
  );
}
