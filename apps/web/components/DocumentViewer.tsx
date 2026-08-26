"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { api, ensureSession } from "../lib/api/client";
import {
  buildHandlers,
  isWebMcpAvailable,
  registerRedactlyTools,
  type ActivityEntry,
  type ToolHandler,
} from "../lib/webmcp/register-tools";
import type { ToolName } from "../lib/webmcp/tool-schemas";
import type { AuditEvent, MaskedDocumentPayload, PendingRequest } from "../lib/types";
import AgentConsole from "./AgentConsole";
import ApprovalModal from "./ApprovalModal";
import AuditTable from "./AuditTable";
import ChallengeModal from "./ChallengeModal";
import MaskedDocument from "./MaskedDocument";

interface WatchResponse {
  changed: boolean;
  signature: string;
  requests: PendingRequest[];
}

export default function DocumentViewer({ slug }: { slug: string }) {
  const [doc, setDoc] = useState<MaskedDocumentPayload | null>(null);
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [pending, setPending] = useState<PendingRequest[]>([]);
  const [activity, setActivity] = useState<ActivityEntry[]>([]);
  const [registered, setRegistered] = useState<ToolName[]>([]);
  const [nativeAvailable, setNativeAvailable] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [handlers, setHandlers] = useState<Record<ToolName, ToolHandler> | null>(null);

  const refresh = useCallback(
    async (signal?: AbortSignal) => {
      const [masked, audit] = await Promise.all([
        api.get<MaskedDocumentPayload>(`/documents/${encodeURIComponent(slug)}/masked`, signal),
        api.get<{ events: AuditEvent[] }>(`/documents/${encodeURIComponent(slug)}/audit?limit=200`, signal),
      ]);
      setDoc(masked);
      setEvents(audit.events);
    },
    [slug],
  );

  // Registration is scoped to this mounted route. Aborting the controller both
  // unregisters the tools and cancels any tool call still waiting on a human.
  useEffect(() => {
    const controller = new AbortController();
    (async () => {
      try {
        await ensureSession();
        const built = buildHandlers({
          slug,
          signal: controller.signal,
          onActivity: (entry) => setActivity((previous) => [...previous.slice(-40), entry]),
        });
        setHandlers(built);
        const names = await registerRedactlyTools({ slug, signal: controller.signal }, built);
        setNativeAvailable(isWebMcpAvailable());
        setRegistered(names);
        await refresh(controller.signal);
      } catch {
        if (!controller.signal.aborted) {
          setError("Could not load this document. Is the policy service running on port 8000?");
        }
      }
    })();
    return () => controller.abort();
  }, [slug, refresh]);

  // Long poll the owner queue. An unmask tool call blocks server-side while it
  // waits, so the modal has to appear in about a second, not on the next tick of
  // a slow interval.
  useEffect(() => {
    const controller = new AbortController();

    const poll = async (): Promise<void> => {
      let signature: string | null = null;
      while (!controller.signal.aborted) {
        try {
          const query: string = signature ? `&signature=${encodeURIComponent(signature)}` : "";
          const response: WatchResponse = await api.get<WatchResponse>(
            `/owner/pending/watch?timeout=20${query}`,
            controller.signal,
          );
          signature = response.signature;
          if (response.changed) {
            setPending(response.requests.filter((item: PendingRequest) => item.document_slug === slug));
            await refresh(controller.signal);
          }
        } catch {
          if (controller.signal.aborted) return;
          await new Promise((resolve) => window.setTimeout(resolve, 1200));
        }
      }
    };

    void poll();
    return () => controller.abort();
  }, [slug, refresh]);

  const decide = useCallback(
    async (requestRef: string, approve: boolean, note: string) => {
      setBusy(true);
      try {
        await api.post(`/owner/requests/${encodeURIComponent(requestRef)}/decision`, {
          approve,
          note: note.trim() ? note.trim() : null,
        });
        setPending((previous) => previous.filter((item) => item.request_ref !== requestRef));
        await refresh();
      } catch {
        setError("That decision could not be recorded. The request may have already timed out.");
      } finally {
        setBusy(false);
      }
    },
    [refresh],
  );

  const current = pending[0] ?? null;
  const maskedCount = doc?.fields.filter((field) => field.masked).length ?? 0;
  const visibleCount = (doc?.fields.length ?? 0) - maskedCount;

  return (
    <main className="shell">
      <header className="masthead">
        <div>
          <h1>{doc?.title ?? slug}</h1>
          <p className="tagline">
            <Link href="/">All documents</Link> · {maskedCount} masked field
            {maskedCount === 1 ? "" : "s"}
            {visibleCount > 0 ? ` · ${visibleCount} reclassified in this session` : ""} · view revision{" "}
            {doc?.view_revision ?? "—"}
          </p>
        </div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <span className={nativeAvailable ? "pill live" : "pill absent"}>
            {nativeAvailable ? "document.modelContext detected" : "WebMCP not available in this browser"}
          </span>
          <span className="pill">{registered.length} tools registered</span>
        </div>
      </header>

      {error ? <p className="notice danger">{error}</p> : null}

      <div className="layout">
        <div>
          <section className="panel">
            <div className="panel-head">
              <h2>Masked document</h2>
              <span className="faint">Placeholders are session-scoped; click one to target it</span>
            </div>
            <div className="panel-body">
              {doc ? (
                <MaskedDocument
                  text={doc.masked_text}
                  fields={doc.fields}
                  selected={selected}
                  onSelect={setSelected}
                />
              ) : (
                <p className="muted">Loading…</p>
              )}
            </div>
          </section>

          <section className="panel">
            <div className="panel-head">
              <h2>Agent console</h2>
              <span className="faint">Calls the registered tools</span>
            </div>
            <AgentConsole
              fields={doc?.fields ?? []}
              handlers={handlers}
              nativeAvailable={nativeAvailable}
              selected={selected}
              onSelectField={setSelected}
            />
          </section>
        </div>

        <div>
          <section className="panel">
            <div className="panel-head">
              <h2>Field inventory</h2>
            </div>
            <div className="panel-body tight">
              {doc && doc.fields.length > 0 ? (
                <table className="grid">
                  <thead>
                    <tr>
                      <th>Category</th>
                      <th>Shape</th>
                      <th>State</th>
                    </tr>
                  </thead>
                  <tbody>
                    {doc.fields.map((field) => (
                      <tr key={field.field_ref}>
                        <td>
                          <span className="tag">{field.category}</span>
                          <div>
                            <code>{field.field_ref}</code>
                          </div>
                        </td>
                        <td>{field.safe_context_hint}</td>
                        <td>{field.masked ? "masked" : "visible"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <p className="muted">No detected fields.</p>
              )}
            </div>
          </section>

          <section className="panel">
            <div className="panel-head">
              <h2>Audit trail</h2>
              <span className="faint">{events.length} events</span>
            </div>
            <div className="panel-body tight">
              <AuditTable events={events} />
            </div>
          </section>

          <section className="panel">
            <div className="panel-head">
              <h2>Tool calls this session</h2>
            </div>
            <div className="panel-body tight">
              {activity.length === 0 ? (
                <p className="muted">No tool calls yet.</p>
              ) : (
                <table className="grid">
                  <tbody>
                    {[...activity].reverse().map((entry) => (
                      <tr key={`${entry.tool}-${entry.at}`}>
                        <td>
                          <code>{entry.tool}</code>
                        </td>
                        <td>{entry.status}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </section>
        </div>
      </div>

      {current?.kind === "UNMASK" ? (
        <ApprovalModal
          request={current}
          busy={busy}
          onDecide={(approve, note) => void decide(current.request_ref, approve, note)}
        />
      ) : null}
      {current?.kind === "CHALLENGE" ? (
        <ChallengeModal
          request={current}
          busy={busy}
          onDecide={(approve, note) => void decide(current.request_ref, approve, note)}
        />
      ) : null}
    </main>
  );
}
