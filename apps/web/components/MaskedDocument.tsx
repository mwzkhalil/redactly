"use client";

import { Fragment, useMemo, type ReactNode } from "react";
import type { FieldSummary } from "../lib/types";

const PLACEHOLDER = /\[\[REDACTED:([A-Z_]+):(fld_[A-Za-z0-9_-]+)\]\]/g;

interface Props {
  text: string;
  fields: FieldSummary[];
  selected: string | null;
  onSelect: (fieldRef: string) => void;
}

/**
 * Renders the masked text the server produced.
 *
 * This component never receives a raw value. The chips are placeholders parsed
 * out of the served string, which is the same string the agent's view tool
 * returns — there is no hidden attribute or data payload holding the real text.
 */
export default function MaskedDocument({ text, fields, selected, onSelect }: Props) {
  const hints = useMemo(
    () => new Map(fields.map((field) => [field.field_ref, field.safe_context_hint])),
    [fields],
  );

  const nodes = useMemo(() => {
    const output: ReactNode[] = [];
    let cursor = 0;
    let key = 0;
    PLACEHOLDER.lastIndex = 0;

    for (let match = PLACEHOLDER.exec(text); match !== null; match = PLACEHOLDER.exec(text)) {
      if (match.index > cursor) {
        output.push(<Fragment key={`t${key++}`}>{text.slice(cursor, match.index)}</Fragment>);
      }
      const [, category, fieldRef] = match;
      output.push(
        <button
          key={`p${key++}`}
          type="button"
          className={`placeholder${selected === fieldRef ? " selected" : ""}`}
          title={hints.get(fieldRef) ?? "redacted field"}
          onClick={() => onSelect(fieldRef)}
        >
          {category}
          <span className="ref">{fieldRef.slice(4, 10)}</span>
        </button>,
      );
      cursor = match.index + match[0].length;
    }

    if (cursor < text.length) {
      output.push(<Fragment key={`t${key++}`}>{text.slice(cursor)}</Fragment>);
    }
    return output;
  }, [text, selected, hints, onSelect]);

  return <pre className="document">{nodes}</pre>;
}
