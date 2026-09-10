import { NextRequest, NextResponse } from "next/server";
import { absoluteUrl } from "@/lib/requestUrl";

// Optional shared-PIN gate for public deployments. Each session drives billed
// Azure Voice Live + Vision calls, so when SITE_ACCESS_PIN is set (typically
// only in the deployed environment, not local dev), visitors must submit it
// once before reaching the app. Leave SITE_ACCESS_PIN unset to disable this
// entirely (e.g. local dev).
const COOKIE_NAME = "site_access_pin";

export function proxy(request: NextRequest) {
  const pin = process.env.SITE_ACCESS_PIN;
  if (!pin) return NextResponse.next();

  if (request.nextUrl.pathname === "/gate") return NextResponse.next();

  const submitted = request.cookies.get(COOKIE_NAME)?.value;
  if (submitted === pin) return NextResponse.next();

  const url = absoluteUrl("/gate", request);
  url.searchParams.set("next", request.nextUrl.pathname);
  return NextResponse.redirect(url);
}

export const config = {
  matcher: ["/((?!gate|api/gate|_next/static|_next/image|favicon.ico).*)"],
};
