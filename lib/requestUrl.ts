import type { NextRequest } from "next/server";

// Container platforms (Azure Container Apps, most reverse proxies) forward
// the original public scheme/host via x-forwarded-* headers; the request's
// own nextUrl/host can reflect the container's internal bind address
// instead (e.g. "0.0.0.0"), which produces broken absolute redirect URLs.
// Always prefer the forwarded headers when building an absolute URL.
export function absoluteUrl(path: string, request: NextRequest): URL {
  const proto = request.headers.get("x-forwarded-proto") ?? request.nextUrl.protocol.replace(":", "");
  const host = request.headers.get("x-forwarded-host") ?? request.headers.get("host") ?? request.nextUrl.host;
  return new URL(path, `${proto}://${host}`);
}
