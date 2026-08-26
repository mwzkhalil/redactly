"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api, ensureSession } from "../lib/api/client";

interface DocumentSummary {
  slug: string;
  title: string;
  processing_status: string;
  field_count: number;
}

export default function DocumentList() {
  const [documents, setDocuments] = useState<DocumentSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    (async () => {
      try {
        await ensureSession();
        const payload = await api.get<{ documents: DocumentSummary[] }>("/documents", controller.signal);
        setDocuments(payload.documents);
      } catch {
        if (!controller.signal.aborted) {
          setError("Could not reach the policy service. Is it running on port 8000?");
        }
      }
    })();
    return () => controller.abort();
  }, []);

  if (error) {
    return <p className="notice danger">{error}</p>;
  }
  if (!documents) {
    return <p className="muted">Loading…</p>;
  }

  return (
    <ul className="doc-list">
      {documents.map((document) => (
        <li key={document.slug}>
          <Link href={`/documents/${document.slug}`}>
            <strong>{document.title}</strong>
            <div className="slug">{document.slug}</div>
            <div className="faint" style={{ marginTop: 6 }}>
              {document.field_count} detected field{document.field_count === 1 ? "" : "s"} ·{" "}
              {document.processing_status.toLowerCase()}
            </div>
          </Link>
        </li>
      ))}
    </ul>
  );
}
