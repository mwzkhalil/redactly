/**
 * The policy service is proxied under the app's own origin so the session cookie
 * can stay `SameSite=Strict`. It also keeps the API off the public origin map:
 * the browser only ever talks to this app.
 */
const apiOrigin = process.env.REDACTLY_API_ORIGIN ?? "http://127.0.0.1:8000";

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // This repo keeps its own agent-facing notes in the README; the generated
  // AGENTS.md/CLAUDE.md files would only add framework boilerplate on top.
  agentRules: false,
  async rewrites() {
    return [{ source: "/api/backend/:path*", destination: `${apiOrigin}/:path*` }];
  },
};

export default nextConfig;
