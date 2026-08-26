import { NextResponse, type NextRequest } from "next/server";

/**
 * Ship the Chrome origin trial token for WebMCP.
 *
 * WebMCP is behind an origin trial (Chrome 149–156). Without a token for the
 * serving origin, `document.modelContext` is absent for every visitor who has
 * not manually enabled a flag, and the page falls back to the polyfill. With
 * one, the native API is available to everyone on a recent Chrome.
 *
 * The token is origin-bound, so it is read at request time rather than baked in
 * at build time: the same image can be deployed to a different origin with a
 * different token and no rebuild. An absent token is not an error — it is the
 * expected state for local development.
 */
const token = process.env.WEBMCP_ORIGIN_TRIAL_TOKEN?.trim();

export function middleware(request: NextRequest) {
  const response = NextResponse.next();
  if (token) {
    response.headers.set("Origin-Trial", token);
  }
  return response;
}

export const config = {
  // Document responses only. The header does nothing on static assets, and the
  // API proxy path must not be rewritten by anything here.
  matcher: ["/", "/documents/:path*"],
};
