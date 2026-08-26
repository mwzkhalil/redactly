import Link from "next/link";
import DocumentList from "../components/DocumentList";
import FrameNotice from "../components/FrameNotice";

export default function HomePage() {
  return (
    <main className="shell">
      <FrameNotice />
      <header className="masthead">
        <div>
          <h1>Redactly</h1>
          <p className="tagline">
            A document viewer that hands an AI agent five real WebMCP tools — read, verify, request an
            unmask, challenge a redaction, read the audit trail — while keeping every sensitive value on
            the server side of a policy boundary.
          </p>
        </div>
        <span className="pill">Synthetic fixtures only</span>
      </header>

      <section className="panel">
        <div className="panel-head">
          <h2>Documents</h2>
          <span className="faint">Open one to register its tools</span>
        </div>
        <div className="panel-body">
          <DocumentList />
        </div>
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>How the boundary works</h2>
        </div>
        <div className="panel-body">
          <p className="muted" style={{ marginTop: 0 }}>
            Original text is stored AES-GCM encrypted and is only ever decrypted inside the policy
            service. The masked view an agent reads contains placeholder ids minted per browser session,
            so a reference from one session means nothing in another. A reveal needs a human decision and
            is delivered <strong>at most once</strong> — a second attempt on the same approval returns{" "}
            <code>already_consumed</code>, because once a value is in an agent&apos;s context there is no
            way to take it back.
          </p>
          <p className="muted" style={{ marginBottom: 0 }}>
            The <Link href="/documents/incident-report-untrusted">incident report fixture</Link> contains
            text written to look like an instruction to the reading agent. Following it changes nothing:
            an agent has no capability to approve its own request.
          </p>
        </div>
      </section>
    </main>
  );
}
