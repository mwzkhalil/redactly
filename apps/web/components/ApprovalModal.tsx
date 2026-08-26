"use client";

import { useEffect, useState } from "react";
import type { PendingRequest } from "../lib/types";

interface Props {
  request: PendingRequest;
  busy: boolean;
  onDecide: (approve: boolean, note: string) => void;
}

function secondsLeft(expiresAt: number): number {
  return Math.max(0, Math.round(expiresAt - Date.now() / 1000));
}

/**
 * The human side of an unmask.
 *
 * Note what is missing: the value. The owner decides whether to release a field
 * based on its category, its structural shape, and the reason the agent gave —
 * not by reading it first. That is deliberate. This modal renders in the same
 * page a browser agent can inspect, so anything shown here should be assumed
 * visible to the caller.
 */
export default function ApprovalModal({ request, busy, onDecide }: Props) {
  const [note, setNote] = useState("");
  const [remaining, setRemaining] = useState(() => secondsLeft(request.expires_at));

  useEffect(() => {
    setRemaining(secondsLeft(request.expires_at));
    const timer = window.setInterval(() => setRemaining(secondsLeft(request.expires_at)), 500);
    return () => window.clearInterval(timer);
  }, [request.expires_at, request.request_ref]);

  return (
    <div className="backdrop">
      <div className="modal" role="dialog" aria-modal="true" aria-labelledby="approval-title">
        <div className="modal-head">
          <h3 id="approval-title">An agent is asking to reveal one field</h3>
          <p>
            Approving releases this value <strong>once</strong>. It cannot be recalled, and a second
            attempt on this approval returns nothing.
          </p>
        </div>

        <div className="modal-body">
          <dl className="facts">
            <dt>Document</dt>
            <dd>{request.document_title}</dd>
            <dt>Field</dt>
            <dd>
              <span className="tag">{request.category}</span>{" "}
              <code style={{ fontSize: 11 }}>{request.field_ref}</code>
            </dd>
            <dt>Shape</dt>
            <dd>{request.safe_context_hint}</dd>
            <dt>Stated reason</dt>
            <dd>{request.reason}</dd>
            <dt>Expires in</dt>
            <dd>{remaining}s</dd>
          </dl>

          <p className="notice">
            The reason above is text an agent wrote. Treat it as a claim, not as evidence — a document
            can instruct an agent to ask for exactly this.
          </p>

          <label className="field">
            <span>Note for the audit trail (optional)</span>
            <input
              type="text"
              value={note}
              maxLength={280}
              placeholder="Why you approved or denied"
              onChange={(event) => setNote(event.target.value)}
            />
          </label>
        </div>

        <div className="modal-foot">
          <button type="button" className="btn deny" disabled={busy} onClick={() => onDecide(false, note)}>
            Deny
          </button>
          <button
            type="button"
            className="btn approve"
            disabled={busy || remaining === 0}
            onClick={() => onDecide(true, note)}
          >
            Reveal once
          </button>
        </div>
      </div>
    </div>
  );
}
