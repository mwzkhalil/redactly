export interface FieldSummary {
  field_ref: string;
  category: string;
  masked: boolean;
  safe_context_hint: string;
}

export interface MaskedDocumentPayload {
  slug: string;
  title: string;
  view_revision: number;
  masked_text: string;
  fields: FieldSummary[];
}

export interface ModelAssessment {
  base_entity_type: string;
  category: string;
  confidence: number;
  confidence_band: string;
}

export interface PendingRequest {
  request_ref: string;
  kind: "UNMASK" | "CHALLENGE";
  reason: string;
  requested_at: number;
  expires_at: number;
  field_ref: string;
  category: string;
  safe_context_hint: string;
  document_slug: string;
  document_title: string;
  model_assessment: ModelAssessment | null;
}

export interface AuditEvent {
  event_id: string;
  actor: "AGENT" | "HUMAN" | "SYSTEM";
  event_type: string;
  outcome: string;
  reason: string | null;
  field_ref: string | null;
  metadata: Record<string, string | number | boolean>;
  occurred_at: number;
}
