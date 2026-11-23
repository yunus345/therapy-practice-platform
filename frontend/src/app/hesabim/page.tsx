"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { ApiError } from "@/lib/api";
import { authErrorMessage } from "@/lib/error-messages";
import { listSessions, revokeSession, type AuthSession } from "@/lib/auth";
import { useSession } from "@/providers/session-provider";

export default function AccountPage() {
  const router = useRouter();
  const { user, loading, logout } = useSession();
  const [sessions, setSessions] = useState<AuthSession[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    if (loading) return;
    if (!user) {
      router.replace("/giris");
      return;
    }
    listSessions()
      .then(setSessions)
      .catch((caught) =>
        setError(
          caught instanceof ApiError
            ? authErrorMessage(caught.code)
            : "Oturumlar yüklenemedi.",
        ),
      );
  }, [loading, user, router]);

  async function revoke(sessionId: string) {
    setError("");
    try {
      await revokeSession(sessionId);
      const revoked = sessions.find((session) => session.id === sessionId);
      if (revoked?.is_current) {
        await logout();
        router.replace("/giris");
      } else {
        setSessions((current) =>
          current.filter((session) => session.id !== sessionId),
        );
      }
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? authErrorMessage(caught.code)
          : "Oturum kapatılamadı.",
      );
    }
  }

  if (loading || !user) return null;

  return (
    <main className="mx-auto max-w-3xl px-4 py-10">
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-semibold">Hesabım</h1>
          <p className="text-muted-foreground">{user.email}</p>
        </div>
        <Button
          variant="outline"
          onClick={async () => {
            await logout();
            router.replace("/giris");
          }}
        >
          Çıkış yap
        </Button>
      </div>
      {error && (
        <Alert variant="destructive" className="mb-4">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      <Card>
        <CardHeader>
          <CardTitle>Aktif oturumlar</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          {sessions.map((session) => (
            <div key={session.id}>
              <div className="flex items-center justify-between gap-4">
                <div>
                  <p className="font-medium">
                    {session.is_current ? "Bu oturum" : "Diğer oturum"}
                  </p>
                  <p className="text-sm text-muted-foreground">
                    {[session.ip_address, session.user_agent]
                      .filter(Boolean)
                      .join(" · ") || "Oturum bilgisi yok"}
                  </p>
                </div>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => revoke(session.id)}
                >
                  Sonlandır
                </Button>
              </div>
              <Separator className="mt-4" />
            </div>
          ))}
        </CardContent>
      </Card>
    </main>
  );
}
