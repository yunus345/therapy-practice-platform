"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { AuthShell } from "@/components/auth/auth-shell";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { ApiError } from "@/lib/api";
import { verifyEmail } from "@/lib/auth";
import { authErrorMessage } from "@/lib/error-messages";

export default function VerifyEmailPage() {
  const [status, setStatus] = useState<"loading" | "success" | "error">(
    "loading",
  );
  const [error, setError] = useState("");

  useEffect(() => {
    const token = new URLSearchParams(
      window.location.hash.replace(/^#/, "?"),
    ).get("token");
    if (!token) {
      queueMicrotask(() => {
        setStatus("error");
        setError("Doğrulama bağlantısı geçersiz.");
      });
      return;
    }
    verifyEmail(token)
      .then(() => setStatus("success"))
      .catch((caught) => {
        setStatus("error");
        setError(
          caught instanceof ApiError
            ? authErrorMessage(caught.code)
            : "İstek tamamlanamadı.",
        );
      });
  }, []);

  return (
    <AuthShell title="E-posta doğrulama">
      {status === "loading" && (
        <Alert>
          <AlertDescription>Doğrulama tamamlanıyor...</AlertDescription>
        </Alert>
      )}
      {status === "success" && (
        <>
          <Alert className="mb-4">
            <AlertDescription>E-posta adresiniz doğrulandı.</AlertDescription>
          </Alert>
          <Link className="text-primary text-sm hover:underline" href="/giris">
            Giriş yap
          </Link>
        </>
      )}
      {status === "error" && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
    </AuthShell>
  );
}
