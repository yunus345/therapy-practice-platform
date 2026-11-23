import { apiFetch } from "./api";
import type { components } from "./api-schema";

type Schemas = components["schemas"];

export type DeliveryMode = Schemas["DeliveryMode"];
export type OfferedService = Schemas["OfferedServiceResponse"];
export type PublicProfile = Schemas["PublicProfileResponse"];
export type Slot = Schemas["SlotResponse"];
export type Slots = Schemas["SlotsResponse"];
export type ClientAppointment = Schemas["ClientAppointmentResponse"];
export type AppointmentPage = Schemas["Page_ClientAppointmentResponse_"];

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
