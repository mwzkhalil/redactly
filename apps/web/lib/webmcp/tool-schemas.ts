/**
 * Tool descriptors.
 *
 * Two things these schemas are *not*. They are not a security boundary — the
 * policy service revalidates every argument, because a caller can post to the
 * API without going through this page at all. And they are not documentation for
 * humans: the descriptions are written for a model deciding whether calling a
 * tool is appropriate, which is why each one states the consequence of the call
 * rather than just its subject.
 */

import type { ModelContextTool } from "../../types/webmcp";

export const PLACEHOLDER_PATTERN = "^fld_[A-Za-z0-9_-]{8,64}$";

const placeholderProperty = {
  type: "string",
  title: "Placeholder id",
  description:
    "A placeholder id taken from the masked document view, formatted [[REDACTED:CATEGORY:fld_...]]. Ids are unique to this browser session and cannot be guessed.",
  pattern: PLACEHOLDER_PATTERN,
  maxLength: 68,
} as const;

const purposeProperty = {
  type: "string",
  title: "Purpose",
  description:
    "Why this call is being made. Recorded verbatim in the audit log and shown to the human owner, so state the task rather than a justification for bypassing redaction.",
  minLength: 1,
  maxLength: 280,
} as const;

const cursorProperty = {
  type: "string",
  title: "Cursor",
  description: "Opaque continuation token from a previous response. Omit to start at the beginning.",
  maxLength: 512,
} as const;

export type ToolName =
  | "request_document_view"
  | "verify_without_reveal"
  | "request_field_unmask"
  | "challenge_redaction"
  | "get_redaction_audit_log";

export interface ToolDescriptor {
  name: ToolName;
  title: string;
  description: string;
  inputSchema: Record<string, unknown>;
  annotations: ModelContextTool["annotations"];
}

export const TOOL_DESCRIPTORS: ToolDescriptor[] = [
  {
    name: "request_document_view",
    title: "Read the masked document",
    description:
      "Returns one page of the currently open document with every sensitive field replaced by a placeholder id. Sensitive values are never included. Follow next_cursor to read further; a stale status means the document was reclassified and reading must restart.",
    inputSchema: {
      type: "object",
      additionalProperties: false,
      properties: { purpose: purposeProperty, cursor: cursorProperty },
      required: ["purpose"],
    },
    annotations: {
      readOnlyHint: true,
      // The document body is attacker-controlled text. Marking it untrusted asks
      // the client to treat it as data rather than as instructions, which matters
      // because a document can and does contain text aimed at the reading agent.
      untrustedContentHint: true,
    },
  },
  {
    name: "verify_without_reveal",
    title: "Check a value against a redacted field",
    description:
      "Answers only whether a value you already hold matches a redacted field. Never returns the field's value. Comparison is format-tolerant within a field type. Attempts are rate limited per field and per session, so this cannot be used to search for a value by trial and error.",
    inputSchema: {
      type: "object",
      additionalProperties: false,
      properties: {
        placeholder_id: placeholderProperty,
        comparison_value: {
          type: "string",
          title: "Value to compare",
          description:
            "The value you already have from another source. It is compared as a keyed digest and is never stored.",
          minLength: 1,
          maxLength: 256,
        },
        purpose: purposeProperty,
      },
      required: ["placeholder_id", "comparison_value", "purpose"],
    },
    // Not read-only: every attempt consumes rate-limit budget and is recorded.
    annotations: { readOnlyHint: false },
  },
  {
    name: "request_field_unmask",
    title: "Ask a human to reveal one field",
    description:
      "Asks the document owner to approve revealing one field, then blocks until they decide or the request times out. If approved, the value is returned exactly once and cannot be retrieved again — there is no way to undo a reveal, so only call this when the masked view genuinely cannot answer the task.",
    inputSchema: {
      type: "object",
      additionalProperties: false,
      properties: {
        placeholder_id: placeholderProperty,
        reason: {
          type: "string",
          title: "Reason for the owner",
          description: "Shown to the human who approves or denies. Be specific about what the value is needed for.",
          minLength: 1,
          maxLength: 280,
        },
      },
      required: ["placeholder_id", "reason"],
    },
    annotations: { readOnlyHint: false },
  },
  {
    name: "challenge_redaction",
    title: "Argue a field was misclassified",
    description:
      "Asks the owner to agree that a field was redacted in error, for example a date or a total mistaken for a personal identifier. If approved, the field becomes visible in this session only and the document view revision changes. Returns the decision and the classifier's assessment, never the value.",
    inputSchema: {
      type: "object",
      additionalProperties: false,
      properties: {
        placeholder_id: placeholderProperty,
        reasoning: {
          type: "string",
          title: "Why this is a false positive",
          description: "The argument the human will review. Reference what the surrounding text shows.",
          minLength: 1,
          maxLength: 280,
        },
      },
      required: ["placeholder_id", "reasoning"],
    },
    annotations: { readOnlyHint: false },
  },
  {
    name: "get_redaction_audit_log",
    title: "Read the redaction audit trail",
    description:
      "Returns a page of the append-only record of every view, verification, unmask request, reveal, and challenge for this document and session. Entries contain outcomes and categories only, never values or comparison guesses.",
    inputSchema: {
      type: "object",
      additionalProperties: false,
      properties: {
        purpose: purposeProperty,
        cursor: cursorProperty,
        limit: {
          type: "integer",
          title: "Maximum events",
          description: "Between 1 and 50.",
          minimum: 1,
          maximum: 50,
        },
      },
      required: ["purpose"],
    },
    annotations: { readOnlyHint: true },
  },
];
