/**
 * WebMCP imperative API surface.
 *
 * `document.modelContext` per the May 2026 spec draft; `navigator.modelContext`
 * is the deprecated alias kept only for feature detection against older builds.
 */

export interface ToolAnnotations {
  readOnlyHint?: boolean;
  untrustedContentHint?: boolean;
}

export interface ToolExecuteCallbackOptions {
  signal: AbortSignal;
}

export interface ModelContextTool {
  name: string;
  title?: string;
  description: string;
  inputSchema?: Record<string, unknown>;
  annotations?: ToolAnnotations;
  execute: (input: Record<string, unknown>, options: ToolExecuteCallbackOptions) => Promise<unknown> | unknown;
}

export interface RegisteredTool {
  name: string;
  title?: string;
  description: string;
  /** An object in current builds; a serialized string in older Chrome. */
  inputSchema?: Record<string, unknown> | string;
  window: Window;
  origin: string;
  annotations?: ToolAnnotations;
}

export interface ModelContextRegisterToolOptions {
  signal?: AbortSignal;
  exposedTo?: string[];
}

export interface ModelContext extends EventTarget {
  registerTool(tool: ModelContextTool, options?: ModelContextRegisterToolOptions): Promise<void>;
  getTools(options?: { fromOrigins?: string[] }): Promise<RegisteredTool[]>;
  /**
   * Chromium execution extension, not part of the core interface, so it is
   * optional and has to be feature detected.
   *
   * `inputJson` is a JSON *string*, not an object. Invalid JSON rejects without
   * invoking the tool's handler, so a mistyped argument here fails in a way that
   * looks like the tool itself broke. The result is a JSON string, or null when
   * the tool resolves without a payload.
   */
  executeTool?(
    tool: RegisteredTool,
    inputJson: string,
    options?: { signal?: AbortSignal },
  ): Promise<string | null>;
}

declare global {
  interface Document {
    modelContext?: ModelContext;
  }
  interface Navigator {
    /** @deprecated Removed in Chromium 150; retained for feature detection only. */
    modelContext?: ModelContext;
  }
}
