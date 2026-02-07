import type { OpenClawPluginApi, ToolDefinition } from "openclaw/plugin-sdk";
import { spawn, type ChildProcess } from "node:child_process";
import path from "node:path";

const memuEnginePlugin = {
  id: "memu-engine",
  name: "memU Agentic Engine",
  kind: "memory",

  register(api: OpenClawPluginApi) {
    const pythonRoot = path.join(__dirname, "python");
    // Get config from context or use env vars/defaults.
    // Note: api.config might not be fully parsed during register phase, recommended to fetch dynamically during tool calls.
    // However, for the background service, we need initial config.
    // Simplified here: assume user has set keys in openclaw.json or process.env.
    
    // ---------------------------------------------------------
    // 1. Cross-Platform Background Service
    // ---------------------------------------------------------
    let syncProcess: ChildProcess | null = null;
    let isShuttingDown = false;

    const startSyncService = (config: any, workspaceDir: string) => {
      if (syncProcess) return; // Already running

      const embeddingConfig = config.embedding || {};
      const ingestConfig = config.ingest || {};
      
      const env = {
        ...process.env,
        PYTHONIOENCODING: "utf-8",
        MEMU_EMBED_API_KEY: embeddingConfig.apiKey || process.env.MEMU_EMBED_API_KEY || "",
        MEMU_EMBED_BASE_URL: embeddingConfig.baseUrl || "https://api.openai.com/v1",
        MEMU_EMBED_MODEL: embeddingConfig.model || "text-embedding-3-small",
        MEMU_DATA_DIR: path.join(workspaceDir, "memU", "data"),
        MEMU_EXTRA_PATHS: JSON.stringify(ingestConfig.extraPaths || []),
        // Auto-infer session dir: usually at workspace sibling agents/main/sessions
        // Assuming standard directory structure here
        OPENCLAW_SESSIONS_DIR: path.join(process.env.HOME || "", ".openclaw/agents/main/sessions")
      };

      const scriptPath = path.join(pythonRoot, "watch_sync.py");
      
      console.log(`[memU] Starting background sync service: ${scriptPath}`);
      
      // Launch using uv run
      syncProcess = spawn("uv", ["run", "--project", pythonRoot, "python", scriptPath], {
        cwd: pythonRoot,
        env,
        stdio: "pipe" // Capture logs
      });

      // Redirect logs to Gateway console (with prefix)
      syncProcess.stdout?.on("data", (d) => {
        const lines = d.toString().trim().split("\n");
        lines.forEach((l: string) => console.log(`[memU Sync] ${l}`));
      });
      syncProcess.stderr?.on("data", (d) => console.error(`[memU Sync Error] ${d}`));

      syncProcess.on("close", (code) => {
        syncProcess = null;
        if (!isShuttingDown) {
          console.warn(`[memU] Sync service crashed (code ${code}). Restarting in 5s...`);
          setTimeout(() => startSyncService(config, workspaceDir), 5000);
        }
      });
    };

    // Use an empty "init" tool or hook to trigger service start.
    // OpenClaw doesn't expose explicit onStart hook to plugins yet.
    // We use lazy loading on first tool call or internal hook.
    
    // Improvement: register an internal hook or lazy load on first memory_search.
    // Using lazy load pattern for robustness.
    
    // ---------------------------------------------------------
    // 2. Register Tools
    // ---------------------------------------------------------
    
    const runPython = async (scriptName: string, args: string[], config: any, workspaceDir: string): Promise<string> => {
      // Key point: Trigger background service here (lazy singleton)
      startSyncService(config, workspaceDir);

      const embeddingConfig = config.embedding || {};
      const extractionConfig = config.extraction || {};
      
      const env = {
        ...process.env,
        PYTHONIOENCODING: "utf-8",
        
        MEMU_EMBED_PROVIDER: embeddingConfig.provider || "openai",
        MEMU_EMBED_API_KEY: embeddingConfig.apiKey || "",
        MEMU_EMBED_BASE_URL: embeddingConfig.baseUrl || "https://api.openai.com/v1",
        MEMU_EMBED_MODEL: embeddingConfig.model || "text-embedding-3-small",
        
        MEMU_CHAT_PROVIDER: extractionConfig.provider || "openai",
        MEMU_CHAT_API_KEY: extractionConfig.apiKey || "",
        MEMU_CHAT_BASE_URL: extractionConfig.baseUrl || "",
        MEMU_CHAT_MODEL: extractionConfig.model || "",

        MEMU_DATA_DIR: path.join(workspaceDir, "memU", "data"),
      };

      return new Promise((resolve) => {
        const proc = spawn("uv", ["run", "--project", pythonRoot, "python", path.join(pythonRoot, "scripts", scriptName), ...args], {
          cwd: pythonRoot,
          env
        });

        let stdout = "";
        let stderr = "";
        proc.stdout.on("data", (data) => { stdout += data.toString(); });
        proc.stderr.on("data", (data) => { stderr += data.toString(); });

        proc.on("close", (code) => {
          if (code !== 0) resolve(`Error (code ${code}): ${stderr}`);
          else resolve(stdout.trim() || "No content found.");
        });
      });
    };

    const searchHandler = async ({ query }: { query: string }, toolCtx: any) => {
      const config = toolCtx.config || {};
      const workspaceDir = toolCtx.agentWorkspace || process.cwd();
      const result = await runPython("search.py", [query], config, workspaceDir);
      return `--- [memU Retrieval System] ---\n${result}`;
    };

    const searchSchema = {
      type: "object",
      additionalProperties: true
    };

    api.registerTool((ctx) => [{
      name: "memu_search",
      description: "Agentic semantic search on the memU long-term database.",
      inputSchema: searchSchema,
      run: (args) => searchHandler(args, ctx)
    }, {
      name: "memory_search",
      description: "Mandatory recall step: semantically search the memory system.",
      inputSchema: searchSchema,
      run: (args) => searchHandler(args, ctx)
    }], { names: ["memu_search", "memory_search"] });

    const getHandler = async ({ path }: { path: string }, toolCtx: any) => {
      const config = toolCtx.config || {};
      const workspaceDir = toolCtx.agentWorkspace || process.cwd();
      return await runPython("get.py", [path], config, workspaceDir);
    };

    const getSchema = {
      type: "object",
      additionalProperties: true
    };

    api.registerTool((ctx) => [{
      name: "memu_get",
      description: "Retrieve content from memU database or workspace disk.",
      inputSchema: getSchema,
      run: (args) => getHandler(args, ctx)
    }, {
      name: "memory_get",
      description: "Read a specific memory Markdown file.",
      inputSchema: getSchema,
      run: (args) => getHandler(args, ctx)
    }], { names: ["memu_get", "memory_get"] });
  }
};

export default memuEnginePlugin;
