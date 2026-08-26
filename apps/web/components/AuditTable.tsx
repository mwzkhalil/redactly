"use client";

import type { AuditEvent } from "../lib/types";

const ACTOR_CLASS: Record<AuditEvent["actor"], string> = {
  AGENT: "tag agent",
  HUMAN: "tag human",
  SYSTEM: "tag system",
};

function formatTime(epochSeconds: number): string {
  return new Date(epochSeconds * 1000).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function formatMetadata(metadata: Record<string, string | number | boolean>): string {
  const entries = Object.entries(metadata);
  if (entries.length === 0) return "—";
  return entries.map(([key, value]) => `${key}=${value}`).join(" ");
}

export default function AuditTable({ events }: { events: AuditEvent[] }) {
  if (events.length === 0) {
    return <p className="muted">Nothing recorded for this document yet.</p>;
  }

  return (
    <div className="scroll">
      <table className="grid">
        <thead>
          <tr>
            <th>Time</th>
            <th>Actor</th>
            <th>Event</th>
            <th>Outcome</th>
            <th>Detail</th>
          </tr>
        </thead>
        <tbody>
          {[...events].reverse().map((event) => (
            <tr key={event.event_id}>
              <td>
                <code>{formatTime(event.occurred_at)}</code>
              </td>
              <td>
                <span className={ACTOR_CLASS[event.actor]}>{event.actor}</span>
              </td>
              <td>
                <span className={event.event_type === "REVEAL_CONSUMED" ? "tag reveal" : undefined}>
                  {event.event_type}
                </span>
              </td>
              <td>{event.outcome}</td>
              <td>
                <code>{formatMetadata(event.metadata)}</code>
                {event.reason ? <div className="faint">{event.reason}</div> : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
