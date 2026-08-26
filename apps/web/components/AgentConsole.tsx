"use client";

import { useMemo, useState } from "react";
import { getModelContext, type ToolHandler } from "../lib/webmcp/register-tools";
import { TOOL_DESCRIPTORS, type ToolName } from "../lib/webmcp/tool-schemas";
import type { FieldSummary } from "../lib/types";

interface Props {
  fields: FieldSummary[];
  handlers: Record<ToolName, ToolHandler> | null;
  nativeAvailable: boolean;
  /**
   * Which field the call targets. Owned by the parent so clicking a placeholder
   * in the document and choosing one here cannot disagree — two SSN fields in the
   * same document are indistinguishable in this dropdown, so pointing at the one
   * you mean in the text is often the only unambiguous way to pick it.
   */
  selected: string | null;
  onSelectField: (fieldRef: string) => void;
}

const NEEDS_PLACEHOLDER = new Set<ToolName>([
  "verify_without_reveal",
  "request_field_unmask",
  "challenge_redaction",
]);

const FREE_TEXT_LABEL: Partial<Record<ToolName, { key: string; label: string; placeholder: string }>> = {
  request_field_unmask: {
    key: "reason",
    label: "Reason shown to the owner",
    placeholder: "Confirming the claimant's contact address before issuing a settlement letter",
  },
  challenge_redaction: {
    key: "reasoning",
    label: "Why this is a false positive",
    placeholder: "This is a settlement total, not a personal identifier",
  },
};

/**
 * In-page harness for driving the registered tools.
 *
 * When the browser supports WebMCP this goes through `getTools()` and
 * `executeTool()`, so what runs is genuinely the registered tool rather than a
 * direct function call. Without WebMCP it falls back to the same handler the
 * tool wraps, which keeps the flow demonstrable in any browser.
 */
export default function AgentConsole({
  fields,
  handlers,
  nativeAvailable,
  selected,
  onSelectField,
}: Props) {
  const maskedFields = useMemo(() => fields.filter((field) => field.masked), [fields]);
  const [tool, setTool] = useState<ToolName>("request_document_view");
  const [freeText, setFreeText] = useState<string>("");
  const [comparison, setComparison] = useState<string>("");
  const [purpose, setPurpose] = useState<string>("Reviewing this claim for completeness");
  const [result, setResult] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [route, setRoute] = useState<string | null>(null);

  const descriptor = TOOL_DESCRIPTORS.find((item) => item.name === tool)!;
  const freeTextConfig = FREE_TEXT_LABEL[tool];
  const requiresPlaceholder = NEEDS_PLACEHOLDER.has(tool);
  // A selected field that has since been reclassified is no longer maskable, so
  // fall back rather than sending a ref the server will reject.
  const stillMasked = maskedFields.some((field) => field.field_ref === selected);
  const activePlaceholder = (stillMasked ? selected : null) ?? maskedFields[0]?.field_ref ?? "";

  async function run() {
    if (!handlers) return;
    setRunning(true);
    setResult(null);

    const input: Record<string, unknown> = {};
    if (tool === "request_document_view" || tool === "get_redaction_audit_log") {
      input.purpose = purpose;
    }
    if (requiresPlaceholder) {
      input.placeholder_id = activePlaceholder;
    }
    if (tool === "verify_without_reveal") {
      input.comparison_value = comparison;
      input.purpose = purpose;
    }
    if (freeTextConfig) {
      input[freeTextConfig.key] = freeText || freeTextConfig.placeholder;
    }

    try {
      const context = getModelContext();
      const registered = context ? (await context.getTools()).find((item) => item.name === tool) : undefined;
      if (context && registered) {
        setRoute("document.modelContext.executeTool()");
        const raw = await context.executeTool(registered, input);
        setResult(prettify(raw));
      } else {
        setRoute("direct handler (WebMCP not available in this browser)");
        setResult(JSON.stringify(await handlers[tool](input), null, 2));
      }
    } catch (error) {
      setResult(JSON.stringify({ status: "unavailable", reason: describe(error) }, null, 2));
    } finally {
      setRunning(false);
    }
  }

  return (
    <div className="panel-body">
      <p className="muted" style={{ marginTop: 0 }}>
        {nativeAvailable
          ? "This browser exposes document.modelContext, so calls below run through the real tool registration."
          : "This browser has no document.modelContext, so calls below invoke the same handler the tool wraps. The security boundary is identical either way — it lives in the policy service."}
      </p>

      <label className="field">
        <span>Tool</span>
        <select value={tool} onChange={(event) => setTool(event.target.value as ToolName)}>
          {TOOL_DESCRIPTORS.map((item) => (
            <option key={item.name} value={item.name}>
              {item.name}
            </option>
          ))}
        </select>
      </label>

      <p className="faint" style={{ marginTop: -4 }}>
        {descriptor.description}
      </p>

      {requiresPlaceholder ? (
        <label className="field">
          <span>Placeholder id</span>
          <select value={activePlaceholder} onChange={(event) => onSelectField(event.target.value)}>
            {maskedFields.map((field) => (
              <option key={field.field_ref} value={field.field_ref}>
                {field.category} · {field.field_ref}
              </option>
            ))}
          </select>
          <span className="faint">
            Or click the placeholder in the document above to target it here.
          </span>
        </label>
      ) : null}

      {tool === "verify_without_reveal" ? (
        <label className="field">
          <span>Value to compare</span>
          <input
            type="text"
            value={comparison}
            maxLength={256}
            placeholder="avery.morgan@example.com"
            onChange={(event) => setComparison(event.target.value)}
          />
        </label>
      ) : null}

      {freeTextConfig ? (
        <label className="field">
          <span>{freeTextConfig.label}</span>
          <textarea
            value={freeText}
            maxLength={280}
            placeholder={freeTextConfig.placeholder}
            onChange={(event) => setFreeText(event.target.value)}
          />
        </label>
      ) : null}

      {tool === "request_document_view" || tool === "get_redaction_audit_log" || tool === "verify_without_reveal" ? (
        <label className="field">
          <span>Purpose (recorded in the audit log)</span>
          <input
            type="text"
            value={purpose}
            maxLength={280}
            onChange={(event) => setPurpose(event.target.value)}
          />
        </label>
      ) : null}

      <div className="button-row">
        <button type="button" className="btn primary" disabled={running || !handlers} onClick={run}>
          {running ? "Waiting…" : `Call ${tool}`}
        </button>
        {result ? (
          <button type="button" className="btn" onClick={() => setResult(null)}>
            Clear
          </button>
        ) : null}
      </div>

      {tool === "request_field_unmask" ? (
        <p className="notice" style={{ marginTop: 14, marginBottom: 0 }}>
          This call blocks until you approve or deny it in the modal that appears. Approving returns the
          value once and only once.
        </p>
      ) : null}

      {result ? (
        <>
          {route ? <p className="faint" style={{ marginBottom: 0, marginTop: 12 }}>via {route}</p> : null}
          <pre className="result">{result}</pre>
        </>
      ) : null}
    </div>
  );
}

function prettify(raw: string): string {
  try {
    return JSON.stringify(JSON.parse(raw), null, 2);
  } catch {
    return raw;
  }
}

function describe(error: unknown): string {
  if (error instanceof DOMException && error.name === "AbortError") return "execution_aborted";
  return "tool_execution_failed";
}
