"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { useState } from "react";
import { AuthShell } from "@/components/auth/auth-shell";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
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
import { register as registerAccount } from "@/lib/auth";
import { errorMessage } from "@/lib/error-messages";

const schema = z
  .object({
    email: z.email("Geçerli bir e-posta adresi girin."),
    password: z.string().min(12, "Şifre en az 12 karakter olmalıdır."),
    passwordConfirm: z.string(),
    first_name: z.string().trim().min(1, "Ad zorunludur."),
    last_name: z.string().trim().min(1, "Soyad zorunludur."),
    phone: z
      .string()
      .regex(
        /^\+90\s?5\d{2}\s?\d{3}\s?\d{2}\s?\d{2}$/,
        "Telefon numarası geçersiz.",
      ),
    kvkk_accepted: z
      .boolean()
      .refine((value) => value === true, "KVKK kabulü gereklidir."),
  })
  .refine((value) => value.password === value.passwordConfirm, {
    path: ["passwordConfirm"],
    message: "Şifreler eşleşmiyor.",
  });

export default function RegisterPage() {
  const router = useRouter();
  const [error, setError] = useState("");
  const form = useForm<z.infer<typeof schema>>({
    resolver: zodResolver(schema),
    defaultValues: {
      email: "",
      password: "",
      passwordConfirm: "",
      first_name: "",
      last_name: "",
      phone: "",
      kvkk_accepted: false,
    },
  });

  async function submit(values: z.infer<typeof schema>) {
    setError("");
    try {
      await registerAccount({
        email: values.email,
        password: values.password,
        first_name: values.first_name,
        last_name: values.last_name,
        phone: values.phone,
        kvkk_accepted: true,
      });
      router.replace("/giris");
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? errorMessage(caught.code)
          : "İstek tamamlanamadı.",
      );
    }
  }

  return (
    <AuthShell title="Hesap oluştur">
      {error && (
        <Alert variant="destructive" className="mb-4">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      <Form {...form}>
        <form onSubmit={form.handleSubmit(submit)} className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-2">
            {(
              [
                ["first_name", "Ad"],
                ["last_name", "Soyad"],
              ] as const
            ).map(([name, label]) => (
              <FormField
                key={name}
                control={form.control}
                name={name}
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>{label}</FormLabel>
                    <FormControl>
                      <Input
                        autoComplete={
                          name === "first_name" ? "given-name" : "family-name"
                        }
                        {...field}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
            ))}
          </div>
          {(
            [
              ["email", "E-posta", "email"],
              ["phone", "Telefon", "tel"],
              ["password", "Şifre", "new-password"],
              ["passwordConfirm", "Şifre tekrar", "new-password"],
            ] as const
          ).map(([name, label, autoComplete]) => (
            <FormField
              key={name}
              control={form.control}
              name={name}
              render={({ field }) => (
                <FormItem>
                  <FormLabel>{label}</FormLabel>
                  <FormControl>
                    <Input
                      type={
                        name === "password" || name === "passwordConfirm"
                          ? "password"
                          : name === "email"
                            ? "email"
                            : "tel"
                      }
                      autoComplete={autoComplete}
                      {...field}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
          ))}
          <FormField
            control={form.control}
            name="kvkk_accepted"
            render={({ field }) => (
              <FormItem>
                <div className="flex items-start gap-3">
                  <Checkbox
                    checked={field.value}
                    onCheckedChange={field.onChange}
                  />
                  <FormLabel className="text-sm leading-5">
                    KVKK aydınlatma metnini okudum ve kabul ediyorum.
                  </FormLabel>
                </div>
                <FormMessage />
              </FormItem>
            )}
          />
          <Button
            type="submit"
            className="w-full"
            disabled={form.formState.isSubmitting}
          >
            Hesap oluştur
          </Button>
        </form>
      </Form>
      <p className="mt-4 text-center text-sm">
        Zaten hesabınız var mı?{" "}
        <Link className="text-primary hover:underline" href="/giris">
          Giriş yap
        </Link>
      </p>
    </AuthShell>
  );
}
