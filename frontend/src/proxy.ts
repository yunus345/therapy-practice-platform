import { NextRequest, NextResponse } from "next/server";

export function proxy(request: NextRequest) {
  const hasSession = request.cookies.has("seans_session");
  if (!hasSession) {
    const loginUrl = new URL("/giris", request.url);
    loginUrl.searchParams.set("next", request.nextUrl.pathname);
    return NextResponse.redirect(loginUrl);
  }
  return NextResponse.next();
}

export const config = {
  matcher: ["/randevularim/:path*", "/hesabim/:path*"],
};
