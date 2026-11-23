"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { AuthShell } from "@/components/auth/auth-shell";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import { ApiError } from "@/lib/api";
import { confirmPasswordReset, requestPasswordReset } from "@/lib/auth";
import { errorMessage } from "@/lib/error-messages";

const requestSchema = z.object({
  email: z.email("Geçerli bir e-posta adresi girin."),
});

const confirmSchema = z
  .object({
    password: z.string().min(12, "Şifre en az 12 karakter olmalıdır."),
    confirmPassword: z.string(),
  })
  .refine((value) => value.password === value.confirmPassword, {
    path: ["confirmPassword"],
    message: "Şifreler eşleşmiyor.",
  });

function PasswordResetContent() {
  const searchParams = useSearchParams();
  const token = searchParams.get("token");
  const [error, setError] = useState("");
  const [sent, setSent] = useState(false);

  const requestForm = useForm<z.infer<typeof requestSchema>>({
    resolver: zodResolver(requestSchema),
    defaultValues: { email: "" },
  });
  const confirmForm = useForm<z.infer<typeof confirmSchema>>({
    resolver: zodResolver(confirmSchema),
    defaultValues: { password: "", confirmPassword: "" },
  });

  async function request(values: z.infer<typeof requestSchema>) {
    setError("");
    try {
      await requestPasswordReset(values.email);
      setSent(true);
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? errorMessage(caught.code)
          : "İstek tamamlanamadı.",
      );
    }
  }

  async function confirm(values: z.infer<typeof confirmSchema>) {
    if (!token) return;
    setError("");
    try {
      await confirmPasswordReset(token, values.password);
      setSent(true);
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? errorMessage(caught.code)
          : "İstek tamamlanamadı.",
      );
    }
  }

  return (
    <AuthShell title="Şifre sıfırlama">
      {error && (
        <Alert variant="destructive" className="mb-4">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      {sent && (
        <Alert className="mb-4">
          <AlertDescription>
            {token
              ? "Şifreniz güncellendi."
              : "E-posta adresine kayıtlı bir hesap varsa sıfırlama bağlantısı gönderildi."}
          </AlertDescription>
        </Alert>
      )}
      {token ? (
        <Form {...confirmForm}>
          <form
            onSubmit={confirmForm.handleSubmit(confirm)}
            className="space-y-4"
          >
            {(
              [
                ["password", "Yeni şifre", "new-password"],
                ["confirmPassword", "Yeni şifre tekrar", "new-password"],
              ] as const
            ).map(([name, label, autoComplete]) => (
              <FormField
                key={name}
                control={confirmForm.control}
                name={name}
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>{label}</FormLabel>
                    <FormControl>
                      <Input
                        type="password"
                        autoComplete={autoComplete}
                        {...field}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
            ))}
            <Button
              type="submit"
              className="w-full"
              disabled={confirmForm.formState.isSubmitting}
            >
              Yeni şifreyi kaydet
            </Button>
          </form>
        </Form>
      ) : (
        <Form {...requestForm}>
          <form
            onSubmit={requestForm.handleSubmit(request)}
            className="space-y-4"
          >
            <FormField
              control={requestForm.control}
              name="email"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>E-posta</FormLabel>
                  <FormControl>
                    <Input type="email" autoComplete="email" {...field} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <Button
              type="submit"
              className="w-full"
              disabled={requestForm.formState.isSubmitting}
            >
              Sıfırlama bağlantısı gönder
            </Button>
          </form>
        </Form>
      )}
      <p className="mt-4 text-center text-sm">
        <Link className="text-primary hover:underline" href="/giris">
          Girişe dön
        </Link>
      </p>
    </AuthShell>
  );
}

export default function PasswordResetPage() {
  return (
    <Suspense
      fallback={
        <AuthShell title="Şifre sıfırlama">
          <p className="text-sm text-muted-foreground">Yükleniyor...</p>
        </AuthShell>
      }
    >
      <PasswordResetContent />
    </Suspense>
  );
}
