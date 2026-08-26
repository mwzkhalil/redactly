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
 * Review of a claimed false positive.
 *
 * This is the one place the classifier's own confidence is shown, because the
 * question being asked is whether the classifier was right. The masked view
 * withholds it: a per-field score would tell an agent which redactions are the
 * weakest and therefore worth attacking first.
 */
export default function ChallengeModal({ request, busy, onDecide }: Props) {
  const [note, setNote] = useState("");
  const [remaining, setRemaining] = useState(() => secondsLeft(request.expires_at));
  const assessment = request.model_assessment;

  useEffect(() => {
    setRemaining(secondsLeft(request.expires_at));
    const timer = window.setInterval(() => setRemaining(secondsLeft(request.expires_at)), 500);
    return () => window.clearInterval(timer);
  }, [request.expires_at, request.request_ref]);

  return (
    <div className="backdrop">
      <div className="modal" role="dialog" aria-modal="true" aria-labelledby="challenge-title">
        <div className="modal-head">
          <h3 id="challenge-title">An agent says this field was redacted in error</h3>
          <p>
            Agreeing unmasks the field for <strong>this browser session only</strong>. It does not change
            the detector&apos;s output or what anyone else sees.
          </p>
        </div>

        <div className="modal-body">
          <dl className="facts">
            <dt>Document</dt>
            <dd>{request.document_title}</dd>
            <dt>Classified as</dt>
            <dd>
              <span className="tag">{request.category}</span>
              {assessment ? (
                <span className="faint">
                  {" "}
                  from label {assessment.base_entity_type} · confidence {assessment.confidence.toFixed(3)}{" "}
                  ({assessment.confidence_band})
                </span>
              ) : null}
            </dd>
            <dt>Shape</dt>
            <dd>{request.safe_context_hint}</dd>
            <dt>Argument</dt>
            <dd>{request.reason}</dd>
            <dt>Expires in</dt>
            <dd>{remaining}s</dd>
          </dl>

          <label className="field">
            <span>Note for the audit trail (optional)</span>
            <input
              type="text"
              value={note}
              maxLength={280}
              placeholder="Why you agreed or disagreed"
              onChange={(event) => setNote(event.target.value)}
            />
          </label>
        </div>

        <div className="modal-foot">
          <button type="button" className="btn deny" disabled={busy} onClick={() => onDecide(false, note)}>
            Keep masked
          </button>
          <button
            type="button"
            className="btn approve"
            disabled={busy || remaining === 0}
            onClick={() => onDecide(true, note)}
          >
            Agree, unmask here
          </button>
        </div>
      </div>
    </div>
  );
}
