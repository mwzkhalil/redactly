/**
 * Tool registration, scoped to the open document.
 *
 * Two properties matter here. Scope: the document is taken from the route, not
 * from a tool argument, so an agent cannot address a document the page is not
 * showing. Lifetime: registration is tied to an AbortSignal, and aborting it
 * both unregisters the tools and cancels any request still waiting on a human —
 * navigating away must not leave an approval prompt on screen for a tool call
 * whose result nobody can receive.
 */

import { api } from "../api/client";
import { safeToolFailure, safeToolResult } from "./safe-tool-result";
import { TOOL_DESCRIPTORS, type ToolName } from "./tool-schemas";
import type { ModelContext, ModelContextTool } from "../../types/webmcp";

export interface ActivityEntry {
  tool: ToolName;
  status: string;
  at: number;
}

export interface RegisterOptions {
  slug: string;
  signal: AbortSignal;
  onActivity?: (entry: ActivityEntry) => void;
}

export type ToolHandler = (
  input: Record<string, unknown>,
  options?: { signal?: AbortSignal },
) => Promise<Record<string, unknown>>;

export function getModelContext(): ModelContext | undefined {
  if (typeof document === "undefined") {
    return undefined;
  }
  // navigator.modelContext is a deprecated alias (removed in Chromium 150) and is
  // only consulted so older builds still work.
  return document.modelContext ?? navigator.modelContext;
}

export function isWebMcpAvailable(): boolean {
  const context = getModelContext();
  return Boolean(context && typeof context.registerTool === "function");
}

/**
 * Combine the teardown signal with the per-execution signal.
 *
 * A tool call can be cancelled two ways: the agent aborts this execution, or the
 * page unmounts. Both must reach the in-flight fetch, otherwise a pending unmask
 * keeps blocking a server-side wait loop.
 */
function linkedSignal(...signals: (AbortSignal | undefined)[]): AbortSignal {
  const controller = new AbortController();
  for (const signal of signals) {
    if (!signal) continue;
    if (signal.aborted) {
      controller.abort(signal.reason);
      break;
    }
    signal.addEventListener("abort", () => controller.abort(signal.reason), { once: true });
  }
  return controller.signal;
}

function documentPath(slug: string, suffix: string): string {
  return `/agent/documents/${encodeURIComponent(slug)}/${suffix}`;
}

export function buildHandlers({ slug, signal, onActivity }: RegisterOptions): Record<ToolName, ToolHandler> {
  const call = async (
    tool: ToolName,
    path: string,
    body: Record<string, unknown>,
    executionSignal?: AbortSignal,
  ): Promise<Record<string, unknown>> => {
    let result: Record<string, unknown>;
    try {
      const payload = await api.post<unknown>(path, body, linkedSignal(signal, executionSignal));
      result = safeToolResult(payload);
    } catch (error) {
      result = safeToolFailure(error);
    }
    onActivity?.({ tool, status: String(result.status ?? "unknown"), at: Date.now() });
    return result;
  };

  return {
    request_document_view: (input, options) =>
      call(
        "request_document_view",
        documentPath(slug, "view"),
        { purpose: input.purpose, cursor: input.cursor ?? null },
        options?.signal,
      ),

    verify_without_reveal: (input, options) =>
      call(
        "verify_without_reveal",
        documentPath(slug, "verify"),
        {
          field_ref: input.placeholder_id,
          comparison_value: input.comparison_value,
          purpose: input.purpose,
        },
        options?.signal,
      ),

    request_field_unmask: (input, options) =>
      call(
        "request_field_unmask",
        documentPath(slug, "unmask"),
        { field_ref: input.placeholder_id, reason: input.reason },
        options?.signal,
      ),

    challenge_redaction: (input, options) =>
      call(
        "challenge_redaction",
        documentPath(slug, "challenge"),
        { field_ref: input.placeholder_id, reasoning: input.reasoning },
        options?.signal,
      ),

    get_redaction_audit_log: (input, options) =>
      call(
        "get_redaction_audit_log",
        documentPath(slug, "audit"),
        {
          purpose: input.purpose,
          cursor: input.cursor ?? null,
          limit: typeof input.limit === "number" ? input.limit : 20,
        },
        options?.signal,
      ),
  };
}

export async function registerRedactlyTools(
  options: RegisterOptions,
  handlers: Record<ToolName, ToolHandler> = buildHandlers(options),
): Promise<ToolName[]> {
  const context = getModelContext();
  if (!context) {
    return [];
  }

  const registered: ToolName[] = [];
  for (const descriptor of TOOL_DESCRIPTORS) {
    const tool: ModelContextTool = {
      name: descriptor.name,
      title: descriptor.title,
      description: descriptor.description,
      inputSchema: descriptor.inputSchema,
      annotations: descriptor.annotations,
      execute: (input, executeOptions) => handlers[descriptor.name](input ?? {}, executeOptions),
    };
    try {
      // registerTool rejects on a duplicate name or an invalid schema, so each
      // registration is awaited rather than fired and forgotten.
      await context.registerTool(tool, { signal: options.signal });
      registered.push(descriptor.name);
    } catch {
      // A single rejected descriptor must not take the other tools down with it.
    }
  }
  return registered;
}
