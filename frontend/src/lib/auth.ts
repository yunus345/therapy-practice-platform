import { apiFetch } from "./api";

export type UserRole = "admin" | "psychologist" | "client";

export type SessionState = {
  csrf_token: string;
  mfa_required: boolean;
  two_factor_setup_required: boolean;
};

export type Me = {
  id: string;
  email: string;
  role: UserRole;
  email_verified: boolean;
  totp_enabled: boolean;
  mfa_required: boolean;
  two_factor_setup_required: boolean;
};

export type Accepted = { status: "accepted" };
export type AuthSession = {
  id: string;
  created_at: string;
  last_seen_at: string;
  expires_at: string;
  ip_address: string | null;
  user_agent: string | null;
  is_current: boolean;
};

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
  return apiFetch<{ provisioning_uri: string }>("/auth/2fa/setup", {
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
  return apiFetch<SessionState & { recovery_codes: string[] }>(
    "/auth/2fa/enable",
    {
      method: "POST",
      body: JSON.stringify({ code }),
    },
  );
}

export function listSessions() {
  return apiFetch<AuthSession[]>("/auth/sessions");
}

export function revokeSession(sessionId: string) {
  return apiFetch<void>(`/auth/sessions/${sessionId}`, { method: "DELETE" });
}
