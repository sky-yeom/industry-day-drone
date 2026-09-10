import { NextRequest, NextResponse } from "next/server";
import { absoluteUrl } from "@/lib/requestUrl";

const COOKIE_NAME = "site_access_pin";

export async function POST(request: NextRequest) {
  const form = await request.formData();
  const pin = String(form.get("pin") ?? "");
  const next = String(form.get("next") ?? "/");

  const expected = process.env.SITE_ACCESS_PIN;
  if (!expected || pin !== expected) {
    const url = absoluteUrl("/gate", request);
    url.searchParams.set("next", next);
    url.searchParams.set("error", "1");
    return NextResponse.redirect(url);
  }

  const url = absoluteUrl(next.startsWith("/") ? next : "/", request);
  const response = NextResponse.redirect(url);
  response.cookies.set(COOKIE_NAME, expected, {
    httpOnly: true,
    sameSite: "lax",
    secure: true,
    path: "/",
    maxAge: 60 * 60 * 12,
  });
  return response;
}
