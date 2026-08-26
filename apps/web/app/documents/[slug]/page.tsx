import DocumentViewer from "../../../components/DocumentViewer";

/**
 * The only route that registers WebMCP tools.
 *
 * Scope comes from the URL. Because the tools are bound to this route, an agent
 * never names a document in a tool argument, so there is no argument to tamper
 * with in order to reach a document the page is not displaying.
 */
export default async function DocumentPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  return <DocumentViewer slug={slug} />;
}
