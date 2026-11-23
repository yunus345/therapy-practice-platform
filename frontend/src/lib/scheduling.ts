import { apiFetch } from "./api";

export type DeliveryMode = "in_person" | "online";

export type OfferedService = {
  id: string;
  name: string;
  description: string | null;
  duration_minutes: number;
  price_amount: string;
  currency: string;
  delivery_mode: DeliveryMode;
};

export type PublicProfile = {
  first_name: string;
  last_name: string;
  title: string | null;
  slug: string;
  bio: string | null;
  timezone: string;
  cancellation_window_hours: number;
  services: OfferedService[];
};

export type Slot = {
  starts_at: string;
  ends_at: string;
};

export type Slots = {
  timezone: string;
  slots: Slot[];
};

export type ClientAppointment = {
  id: string;
  psychologist_name: string;
  psychologist_slug: string;
  service_name: string;
  delivery_mode: DeliveryMode;
  starts_at: string;
  ends_at: string;
  timezone: string;
  status: string;
  price_amount: string;
  currency: string;
  rescheduled_from_id: string | null;
  cancelled_at: string | null;
  cancellation_reason: string | null;
  created_at: string;
};

export type AppointmentPage = {
  items: ClientAppointment[];
  total: number;
  limit: number;
  offset: number;
};

export function getPublicProfile(slug: string) {
  return apiFetch<PublicProfile>(
    `/public/psychologists/${encodeURIComponent(slug)}`,
  );
}

export function getPublicSlots(
  slug: string,
  serviceId: string,
  from: string,
  to: string,
) {
  const params = new URLSearchParams({
    service_id: serviceId,
    from,
    to,
  });
  return apiFetch<Slots>(
    `/public/psychologists/${encodeURIComponent(slug)}/slots?${params}`,
  );
}

export function bookAppointment(
  psychologistSlug: string,
  serviceId: string,
  startsAt: string,
) {
  return apiFetch<ClientAppointment>("/appointments", {
    method: "POST",
    body: JSON.stringify({
      psychologist_slug: psychologistSlug,
      service_id: serviceId,
      starts_at: startsAt,
    }),
  });
}

export function listOwnAppointments(when: "all" | "upcoming" | "past" = "all") {
  return apiFetch<AppointmentPage>(
    `/me/appointments?limit=100&offset=0&when=${when}`,
  );
}

export function cancelOwnAppointment(appointmentId: string) {
  return apiFetch<ClientAppointment>(`/appointments/${appointmentId}/cancel`, {
    method: "POST",
    body: "{}",
  });
}

export function rescheduleOwnAppointment(
  appointmentId: string,
  startsAt: string,
) {
  return apiFetch<ClientAppointment>(
    `/appointments/${appointmentId}/reschedule`,
    {
      method: "POST",
      body: JSON.stringify({ starts_at: startsAt }),
    },
  );
}
