"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { AuthShell } from "@/components/auth/auth-shell";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
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
import {
  enableTwoFactor,
  startTwoFactorSetup,
  verifyTwoFactor,
} from "@/lib/auth";
import { authErrorMessage } from "@/lib/error-messages";
import { useSession } from "@/providers/session-provider";

const codeSchema = z.object({
  code: z.string().regex(/^\d{6}$/, "Altı haneli kod girin."),
});

const recoverySchema = z.object({
  recoveryCode: z.string().min(1, "Kurtarma kodu girin."),
});

export default function TwoFactorPage() {
  const router = useRouter();
  const { user, loading, refresh } = useSession();
  const [error, setError] = useState("");
  const [provisioningUri, setProvisioningUri] = useState("");
  const [recoveryCodes, setRecoveryCodes] = useState<string[]>([]);
  const codeForm = useForm<z.infer<typeof codeSchema>>({
    resolver: zodResolver(codeSchema),
    defaultValues: { code: "" },
  });
  const recoveryForm = useForm<z.infer<typeof recoverySchema>>({
    resolver: zodResolver(recoverySchema),
    defaultValues: { recoveryCode: "" },
  });
  const setupRequired = Boolean(user?.two_factor_setup_required);
  const verifyRequired = Boolean(user?.mfa_required);

  useEffect(() => {
    if (loading) return;
    if (!setupRequired || provisioningUri) return;
    startTwoFactorSetup()
      .then((setup) => setProvisioningUri(setup.provisioning_uri))
      .catch((caught) =>
        setError(
          caught instanceof ApiError
            ? authErrorMessage(caught.code)
            : "İstek tamamlanamadı.",
        ),
      );
  }, [loading, setupRequired, provisioningUri]);

  useEffect(() => {
    if (loading) return;
    if (!user) {
      router.replace("/giris");
      return;
    }
    if (!setupRequired && !verifyRequired) void refresh();
  }, [loading, user, setupRequired, verifyRequired, refresh, router]);

  async function enable(values: z.infer<typeof codeSchema>) {
    setError("");
    try {
      const enabled = await enableTwoFactor(values.code);
      setRecoveryCodes(enabled.recovery_codes);
      await refresh();
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? authErrorMessage(caught.code)
          : "İstek tamamlanamadı.",
      );
    }
  }

  async function verify(values: z.infer<typeof codeSchema>) {
    setError("");
    try {
      await verifyTwoFactor(values.code);
      await refresh();
      router.replace("/");
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? authErrorMessage(caught.code)
          : "İstek tamamlanamadı.",
      );
    }
  }

  async function recover(values: z.infer<typeof recoverySchema>) {
    setError("");
    try {
      await verifyTwoFactor(undefined, values.recoveryCode);
      await refresh();
      router.replace("/");
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? authErrorMessage(caught.code)
          : "İstek tamamlanamadı.",
      );
    }
  }

  if (!setupRequired && !verifyRequired) {
    return (
      <AuthShell title="İki adımlı doğrulama">
        <Button onClick={() => router.replace("/")}>Ana sayfaya dön</Button>
      </AuthShell>
    );
  }

  if (recoveryCodes.length) {
    return (
      <AuthShell title="Kurtarma kodları">
        <Alert className="mb-4">
          <AlertDescription>
            Bu kodları güvenli bir yerde saklayın. Her kod yalnızca bir kez
            kullanılabilir.
          </AlertDescription>
        </Alert>
        <div className="grid grid-cols-2 gap-2 rounded-md border p-4 font-mono text-sm">
          {recoveryCodes.map((code) => (
            <span key={code}>{code}</span>
          ))}
        </div>
        <Button className="mt-4 w-full" onClick={() => router.replace("/")}>
          Devam et
        </Button>
      </AuthShell>
    );
  }

  return (
    <AuthShell title="İki adımlı doğrulama">
      {error && (
        <Alert variant="destructive" className="mb-4">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      {setupRequired ? (
        <>
          {provisioningUri && (
            <Alert className="mb-4">
              <AlertDescription>
                Doğrulayıcı uygulama adresi:{" "}
                <span className="break-all">{provisioningUri}</span>
              </AlertDescription>
            </Alert>
          )}
          <Form {...codeForm}>
            <form
              onSubmit={codeForm.handleSubmit(enable)}
              className="space-y-4"
            >
              <FormField
                control={codeForm.control}
                name="code"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Altı haneli kod</FormLabel>
                    <FormControl>
                      <Input
                        inputMode="numeric"
                        autoComplete="one-time-code"
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
                disabled={codeForm.formState.isSubmitting}
              >
                Doğrulamayı etkinleştir
              </Button>
            </form>
          </Form>
        </>
      ) : (
        <Tabs defaultValue="code">
          <TabsList className="grid w-full grid-cols-2">
            <TabsTrigger value="code">Doğrulayıcı kodu</TabsTrigger>
            <TabsTrigger value="recovery">Kurtarma kodu</TabsTrigger>
          </TabsList>
          <TabsContent value="code" className="mt-4">
            <Form {...codeForm}>
              <form
                onSubmit={codeForm.handleSubmit(verify)}
                className="space-y-4"
              >
                <FormField
                  control={codeForm.control}
                  name="code"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Altı haneli kod</FormLabel>
                      <FormControl>
                        <Input
                          inputMode="numeric"
                          autoComplete="one-time-code"
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
                  disabled={codeForm.formState.isSubmitting}
                >
                  Doğrulamayı tamamla
                </Button>
              </form>
            </Form>
          </TabsContent>
          <TabsContent value="recovery" className="mt-4">
            <Form {...recoveryForm}>
              <form
                onSubmit={recoveryForm.handleSubmit(recover)}
                className="space-y-4"
              >
                <FormField
                  control={recoveryForm.control}
                  name="recoveryCode"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Kurtarma kodu</FormLabel>
                      <FormControl>
                        <Input {...field} />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
                <Button
                  type="submit"
                  className="w-full"
                  disabled={recoveryForm.formState.isSubmitting}
                >
                  Giriş yap
                </Button>
              </form>
            </Form>
          </TabsContent>
        </Tabs>
      )}
    </AuthShell>
  );
}
