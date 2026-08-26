# Deploying Redactly on your own server

One container, one public port. Next.js is the front door and uvicorn listens on
container loopback, unreachable from outside.

## Why one origin, in one container

This constraint decides the deployment shape, so it comes before the commands.

`request_field_unmask` blocks server-side until a human approves or denies, and
the owner queue long-polls in 20-second windows. Anything in the request path
with a short timeout will cut an approval off mid-wait — killing the exact
behaviour the project exists to demonstrate. **This is the one thing most likely
to break your deploy**, and it is a reverse proxy setting, not an app bug. See
the nginx note below.

Splitting the viewer and API across two origins would avoid the long request,
but then the session cookie can no longer be `SameSite=Strict` — it would need
`SameSite=None`, which Chrome's third-party cookie rules may refuse outright.

So the deployed topology is identical to local development, which means what you
run in production is what was tested.

## Requirements

- Docker with Compose v2.
- A domain pointed at the server.
- **TLS.** Not optional: WebMCP requires a secure context, so over plain HTTP
  `document.modelContext` never appears and the tools cannot register. The only
  exception is `localhost`.

## Run it

```bash
git clone <your-repo-url> redactly
cd redactly
docker compose up -d --build
docker compose logs -f
```

The first build downloads Node and Python dependencies and bakes the ONNX
detector into the image, so expect several minutes. Baking the model is
deliberate: the first visitor should not wait on a model fetch, and a running
container should not depend on the Hugging Face hub being reachable.

Confirm it came up healthy:

```bash
curl -fsS http://127.0.0.1:7860/api/backend/health
docker compose logs | grep -E 'detector_ready|fixtures_seeded'
```

You want `detector_ready` with `"detector": "onnx"` and `fixtures_seeded` with
`"count": 3`. If the detector failed, documents are stored `FAILED` with no
fields and the app refuses to build a view from them — it fails closed rather
than rendering an unscanned document as though it had been scanned and found
clean. So a broken detector looks like an empty document list, not a leak.

## Reverse proxy

### Caddy — recommended

Automatic certificates and no timeout to get wrong:

```caddyfile
redactly.example.com {
    reverse_proxy 127.0.0.1:7860
}
```

### nginx — mind the timeout

nginx defaults `proxy_read_timeout` to 60 seconds, which is **shorter than the
approval window**. Left at the default, a reviewer taking their time will see
the tool call fail rather than return a value.

```nginx
server {
    listen 443 ssl;
    server_name redactly.example.com;

    # ssl_certificate / ssl_certificate_key via certbot

    location / {
        proxy_pass http://127.0.0.1:7860;
        proxy_http_version 1.1;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Must exceed REDACTLY_APPROVAL_TIMEOUT_SECONDS. A blocked unmask is a
        # feature, and this is the setting that decides whether it survives.
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;

        # The owner queue long-polls; buffering delays the approval modal.
        proxy_buffering off;
    }
}
```

## Making WebMCP native rather than polyfilled

WebMCP is in a Chrome origin trial running from Chrome 149 to 156, ending
17 November 2026. Without a token, `document.modelContext` does not exist for a
visitor unless they manually enabled `chrome://flags/#enable-webmcp-testing`.

1. Register `https://redactly.example.com` at
   <https://developer.chrome.com/origintrials> for the WebMCP trial. The token is
   bound to that exact origin, scheme and host included.
2. Put it in a `.env` beside `compose.yaml`:

```dotenv
WEBMCP_ORIGIN_TRIAL_TOKEN=<token>
```

3. `docker compose up -d` — no rebuild needed. `middleware.ts` reads the token at
   request time and emits an `Origin-Trial` response header, so rotating it or
   moving domain is a restart, not a rebuild.

Without a token the page installs `@mcp-b/webmcp-polyfill`, which provides a
real `document.modelContext`: tools are genuinely registered through
`registerTool`, and `getTools`/`executeTool` work. The header pill reports which
one is active, because "the boundary holds" and "the browser shipped the API" are
different claims and only the first is ours to make.

## Configuration

Everything is read from the environment with a `REDACTLY_` prefix; see
`services/api/app/config.py`. What matters in production:

| Variable | Default in the image | Notes |
| --- | --- | --- |
| `REDACTLY_MASTER_KEY` | generated into the state volume | Base64 32 bytes. Set it explicitly if you ever need to move the database between hosts |
| `REDACTLY_COOKIE_SECURE` | `1` | Correct behind TLS; the cookie will not be sent over plain HTTP |
| `REDACTLY_APPROVAL_TIMEOUT_SECONDS` | `180` via compose | Keep the proxy read timeout above this |
| `REDACTLY_GRANT_TTL_SECONDS` | `60` | Window between approval and delivery |
| `REDACTLY_VERIFY_MAX_PER_FIELD` | `5` | Verification attempts per field per window |
| `WEBMCP_ORIGIN_TRIAL_TOKEN` | unset | See above |

### About the generated key

Leaving `REDACTLY_MASTER_KEY` unset generates one into the state volume on first
boot. That is safe here only because the key and the database live in the same
volume and are therefore discarded together — a key that outlives its ciphertext,
or vice versa, is the failure worth avoiding. If you ever back up the database,
set the key explicitly and store it separately.

## Verify the live deploy end to end

```bash
cd services/api
uv run python scripts/smoke_demo.py --base-url https://redactly.example.com/api/backend
```

This plays the agent and the owner against your live origin and prints every
response, including the at-most-once reveal and the `already_consumed` replay.

## Updating

```bash
git pull
docker compose up -d --build
```

The state volume survives, so documents keep their existing ciphertext and the
master key still matches. To start clean instead:

```bash
docker compose down -v && docker compose up -d --build
```

## Public demo, private data

The fixtures are synthetic and the README says so on the page. Sessions isolate
visitors from each other, so a shared instance is fine — each visitor approves
only their own requests and sees only their own audit trail.

The honest caveat is unchanged by deployment: approval happens in a modal on the
same page an agent can drive, so a browser-resident agent could observe the queue
or race the click. Production handling real data would move approval to an
out-of-band authenticated channel, and would add real authentication and document
ownership in place of session-cookie isolation.
