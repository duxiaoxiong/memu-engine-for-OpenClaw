// Type declarations for OpenClaw plugin SDK
declare module "openclaw/plugin-sdk" {
  export interface OpenClawPluginApi {
    id: string;
    pluginConfig?: Record<string, unknown>;
    registerTool(
      factory: (ctx: ToolContext) => ToolDefinition[],
      options?: { names?: string[] }
    ): void;
  }

  export interface ToolContext {
    config?: {
      plugins?: {
        entries?: Record<string, { config?: Record<string, unknown> }>;
      };
    };
    workspaceDir?: string;
  }

  export interface ToolDefinition {
    name: string;
    description: string;
    parameters: object;
    execute(toolCallId: string, params: unknown): Promise<ToolResult>;
  }

  export interface ToolResult {
    content: Array<{ type: string; text: string }>;
    details?: Record<string, unknown>;
  }
}
