import { apiFetch } from "./api";
import type { components } from "./api-schema";

type Schemas = components["schemas"];

export type UserRole = Schemas["UserRole"];
export type SessionState = Schemas["SessionStateResponse"];
export type Me = Schemas["MeResponse"];
export type Accepted = Schemas["AcceptedResponse"];
export type AuthSession = Schemas["SessionResponse"];
export type TwoFactorSetup = Schemas["TwoFactorSetupResponse"];
export type TwoFactorEnabled = Schemas["TwoFactorEnabledResponse"];

export function getMe() {
  return apiFetch<Me>("/auth/me");
}

export function login(email: string, password: string) {
  return apiFetch<SessionState>("/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
}

export function register(input: {
  email: string;
  password: string;
  first_name: string;
  last_name: string;
  phone: string;
  kvkk_accepted: true;
}) {
  return apiFetch<Accepted>("/auth/register", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function logout() {
  return apiFetch<void>("/auth/logout", { method: "POST" });
}

export function requestPasswordReset(email: string) {
  return apiFetch<Accepted>("/auth/password-reset/request", {
    method: "POST",
    body: JSON.stringify({ email }),
  });
}

export function confirmPasswordReset(token: string, new_password: string) {
  return apiFetch<void>("/auth/password-reset/confirm", {
    method: "POST",
    body: JSON.stringify({ token, new_password }),
  });
}

export function verifyEmail(token: string) {
  return apiFetch<void>("/auth/verify-email", {
    method: "POST",
    body: JSON.stringify({ token }),
  });
}

export function startTwoFactorSetup() {
  return apiFetch<TwoFactorSetup>("/auth/2fa/setup", {
    method: "POST",
  });
}

export function verifyTwoFactor(code?: string, recovery_code?: string) {
  return apiFetch<SessionState>("/auth/2fa/verify", {
    method: "POST",
    body: JSON.stringify({
      code: code || null,
      recovery_code: recovery_code || null,
    }),
  });
}

export function enableTwoFactor(code: string) {
  return apiFetch<TwoFactorEnabled>("/auth/2fa/enable", {
    method: "POST",
    body: JSON.stringify({ code }),
  });
}

export function listSessions() {
  return apiFetch<AuthSession[]>("/auth/sessions");
}

export function revokeSession(sessionId: string) {
  return apiFetch<void>(`/auth/sessions/${sessionId}`, { method: "DELETE" });
}
