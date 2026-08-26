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
  inputSchema?: string;
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
  executeTool(
    tool: RegisteredTool,
    inputObject?: Record<string, unknown>,
    options?: { signal?: AbortSignal },
  ): Promise<string>;
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
