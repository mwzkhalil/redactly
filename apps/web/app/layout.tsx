import type { Metadata } from "next";
import type { ReactNode } from "react";
import "./globals.css";

export const metadata: Metadata = {
  title: "Redactly",
  description:
    "A document viewer that exposes real WebMCP tools to an AI agent without letting a sensitive value reach the DOM, a log, or an agent's context by accident.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
