"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import Link from "next/link";
import { useRouter } from "next/navigation";
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
import { errorMessage } from "@/lib/error-messages";
import { login } from "@/lib/auth";
import { useSession } from "@/providers/session-provider";
import { useState } from "react";

const schema = z.object({
  email: z.email("Geçerli bir e-posta adresi girin."),
  password: z.string().min(1, "Şifre zorunludur."),
});

export default function LoginPage() {
  const router = useRouter();
  const { refresh } = useSession();
  const [error, setError] = useState("");
  const form = useForm<z.infer<typeof schema>>({
    resolver: zodResolver(schema),
    defaultValues: { email: "", password: "" },
  });

  async function submit(values: z.infer<typeof schema>) {
    setError("");
    try {
      const state = await login(values.email, values.password);
      await refresh();
      if (state.mfa_required) router.replace("/2fa");
      else if (state.two_factor_setup_required) router.replace("/2fa");
      else router.replace("/");
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? errorMessage(caught.code)
          : "İstek tamamlanamadı.",
      );
    }
  }

  return (
    <AuthShell title="Giriş yap">
      {error && (
        <Alert variant="destructive" className="mb-4">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      <Form {...form}>
        <form onSubmit={form.handleSubmit(submit)} className="space-y-5">
          <FormField
            control={form.control}
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
          <FormField
            control={form.control}
            name="password"
            render={({ field }) => (
              <FormItem>
                <FormLabel>Şifre</FormLabel>
                <FormControl>
                  <Input
                    type="password"
                    autoComplete="current-password"
                    {...field}
                  />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          <Button
            type="submit"
            className="w-full"
            disabled={form.formState.isSubmitting}
          >
            Giriş yap
          </Button>
          <div className="flex justify-between text-sm">
            <Link className="text-primary hover:underline" href="/kayit">
              Hesap oluştur
            </Link>
            <Link
              className="text-primary hover:underline"
              href="/sifre-sifirlama"
            >
              Şifremi unuttum
            </Link>
          </div>
        </form>
      </Form>
    </AuthShell>
  );
}
