/**
 * Result shaping.
 *
 * Whatever a tool returns lands in an agent's context and may be quoted into a
 * summary or passed to another tool. So the browser side does not forward
 * responses verbatim: it copies an explicit allowlist of keys and turns every
 * failure into a flat status, because an unhandled exception message can carry a
 * URL, a stack frame, or a fragment of a response body.
 */

const ALLOWED_KEYS = new Set([
  "status",
  "reason",
  "detail",
  "matches",
  "category",
  "attempts_remaining",
  "retry_after_seconds",
  "scope",
  "title",
  "masked_text",
  "fields",
  "field_ref",
  "masked",
  "safe_context_hint",
  "page_index",
  "page_count",
  "has_more",
  "next_cursor",
  "view_revision",
  "value",
  "reveal_receipt_id",
  "consumed",
  "request_ref",
  "model_assessment",
  "base_entity_type",
  "confidence",
  "confidence_band",
  "events",
  "event_id",
  "event_type",
  "actor",
  "outcome",
  "metadata",
  "occurred_at",
  "masked_field_count",
  "visible_field_count",
]);

const MAX_DEPTH = 4;
const MAX_ARRAY_LENGTH = 60;
const MAX_STRING_LENGTH = 20_000;

function sanitiseValue(value: unknown, depth: number): unknown {
  if (value === null || typeof value === "boolean" || typeof value === "number") {
    return value;
  }
  if (typeof value === "string") {
    return value.length > MAX_STRING_LENGTH ? `${value.slice(0, MAX_STRING_LENGTH)}…` : value;
  }
  if (depth >= MAX_DEPTH) {
    return undefined;
  }
  if (Array.isArray(value)) {
    return value.slice(0, MAX_ARRAY_LENGTH).map((item) => sanitiseValue(item, depth + 1));
  }
  if (typeof value === "object") {
    return sanitiseObject(value as Record<string, unknown>, depth + 1);
  }
  return undefined;
}

function sanitiseObject(input: Record<string, unknown>, depth: number): Record<string, unknown> {
  const output: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(input)) {
    if (!ALLOWED_KEYS.has(key)) {
      continue;
    }
    const sanitised = sanitiseValue(value, depth);
    if (sanitised !== undefined) {
      output[key] = sanitised;
    }
  }
  return output;
}

export function safeToolResult(payload: unknown): Record<string, unknown> {
  if (payload === null || typeof payload !== "object" || Array.isArray(payload)) {
    return { status: "unavailable", reason: "unexpected_response" };
  }
  const result = sanitiseObject(payload as Record<string, unknown>, 0);
  return "status" in result ? result : { ...result, status: "ok" };
}

/**
 * Collapse any thrown value into a terminal status.
 *
 * Fail closed: an agent that cannot tell "denied" from "the service is down"
 * should assume it was not granted anything.
 */
export function safeToolFailure(error: unknown): Record<string, unknown> {
  if (error instanceof DOMException && error.name === "AbortError") {
    return { status: "cancelled", reason: "execution_aborted" };
  }
  if (typeof error === "object" && error !== null && "status" in error) {
    const status = (error as { status: unknown }).status;
    if (status === 401) {
      return { status: "unavailable", reason: "no_active_session" };
    }
    if (status === 404) {
      return { status: "not_found", reason: "unknown_reference" };
    }
    if (status === 422) {
      return { status: "unavailable", reason: "invalid_arguments" };
    }
  }
  return { status: "unavailable", reason: "policy_service_error" };
}
