/**
 * The policy service is proxied under the app's own origin so the session cookie
 * can stay `SameSite=Strict`. It also keeps the API off the public origin map:
 * the browser only ever talks to this app.
 *
 * This is load-bearing for deployment, not just tidiness. `request_field_unmask`
 * blocks server-side until a human decides, so the proxy has to be a real Node
 * server with no request ceiling — a serverless edge function would kill the
 * approval mid-wait. Keeping one origin also avoids a `SameSite=None` cookie,
 * which Chrome's third-party cookie rules may refuse outright.
 */
const apiOrigin = process.env.REDACTLY_API_ORIGIN ?? "http://127.0.0.1:8000";

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Self-contained server bundle, so the runtime image does not need node_modules.
  output: "standalone",
  // This repo keeps its own agent-facing notes in the README; the generated
  // AGENTS.md/CLAUDE.md files would only add framework boilerplate on top.
  agentRules: false,
  async rewrites() {
    return [{ source: "/api/backend/:path*", destination: `${apiOrigin}/:path*` }];
  },
};

export default nextConfig;
