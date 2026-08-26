/**
 * Resolve a `document.modelContext` to register against.
 *
 * The spec draft has moved three times in five months, so every mention of the
 * platform API is confined to this file and `register-tools.ts`. Nothing else in
 * the app knows whether the context is native or polyfilled.
 *
 * Native support only exists in Chrome behind an origin trial token or a local
 * flag. Rather than degrade to a bespoke fallback, we install the polyfill so
 * the tools are genuinely registered and an MCP-B style agent can drive them.
 * The distinction still matters for honesty, so it is reported rather than
 * hidden: a polyfilled context proves the boundary works, not that the browser
 * shipped the API.
 */

export type ContextMode = "native" | "polyfill" | "unavailable";

let resolved: ContextMode | null = null;

export async function ensureModelContext(): Promise<ContextMode> {
  if (typeof document === "undefined") {
    return "unavailable";
  }
  if (resolved) {
    return resolved;
  }

  if (document.modelContext ?? navigator.modelContext) {
    resolved = "native";
    return resolved;
  }

  try {
    const { initializeWebMCPPolyfill } = await import("@mcp-b/webmcp-polyfill");
    // A no-op when something else already owns the context, so this cannot
    // clobber a native implementation that appeared between the check and here.
    initializeWebMCPPolyfill();
    resolved = document.modelContext ? "polyfill" : "unavailable";
  } catch {
    // The page has to keep working without it; the policy service is the
    // boundary either way, and the in-page console still exercises it.
    resolved = "unavailable";
  }
  return resolved;
}
