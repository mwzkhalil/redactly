"use client";

import { useEffect, useState } from "react";

/**
 * Warn when the app is running inside a cross-origin frame.
 *
 * Hugging Face embeds a Space in an iframe on huggingface.co. Two things break
 * there, both silently: the session cookie is `SameSite=Strict` so the browser
 * withholds it in a third-party context, and WebMCP refuses to register tools in
 * a cross-origin frame without an `allow="tools"` attribute the embedder would
 * have to set. Neither produces a useful error, so the page would just look
 * broken. Saying so is better than letting someone conclude the demo is dead.
 */
export default function FrameNotice() {
  const [framed, setFramed] = useState(false);

  useEffect(() => {
    try {
      setFramed(window.self !== window.top);
    } catch {
      // A cross-origin parent throws on access, which is itself the answer.
      setFramed(true);
    }
  }, []);

  if (!framed) {
    return null;
  }

  return (
    <p className="notice danger" style={{ margin: "0 0 18px" }}>
      This page is embedded in a frame, where the browser withholds the session
      cookie and WebMCP will not register tools.{" "}
      <a href={typeof window === "undefined" ? "#" : window.location.href} target="_blank" rel="noreferrer">
        Open it directly
      </a>{" "}
      to use the demo.
    </p>
  );
}
