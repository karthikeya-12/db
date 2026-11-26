import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

/**
 * Sends visitors without a session cookie to the login page. This is only a convenience redirect:
 * the API checks the cookie's signature on every request, so a forged cookie gets nothing.
 */
export function proxy(request: NextRequest) {
  const { pathname, search } = request.nextUrl;
  if (pathname === "/login" || pathname === "/signup" || request.cookies.has("archive_session")) return NextResponse.next();

  const url = request.nextUrl.clone();
  url.pathname = "/login";
  url.search = pathname === "/" ? "" : `?next=${encodeURIComponent(pathname + search)}`;
  return NextResponse.redirect(url);
}

export const config = {
  // Everything except the API (proxied to the backend), Next internals and files with an extension.
  matcher: ["/((?!api|_next/static|_next/image|.*\\..*).*)"],
};
