import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { spawn, type ChildProcess } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

const memuEnginePlugin = {
  id: "memu-engine",
  name: "memU Agentic Engine",
  kind: "memory",

  register(api: OpenClawPluginApi) {
    const pythonRoot = path.join(__dirname, "python");

    const computeExtraPaths = (pluginConfig: any, workspaceDir: string): string[] => {
      const ingestConfig = pluginConfig?.ingest || {};
      const includeDefaultPaths = ingestConfig.includeDefaultPaths !== false;

      const defaultPaths = [
        path.join(workspaceDir, "AGENTS.md"),
        path.join(workspaceDir, "SOUL.md"),
        path.join(workspaceDir, "TOOLS.md"),
        path.join(workspaceDir, "MEMORY.md"),
        path.join(workspaceDir, "HEARTBEAT.md"),
        path.join(workspaceDir, "BOOTSTRAP.md"),
        // OpenClaw canonical durable memory folder
        path.join(workspaceDir, "memory"),
      ];

      const extraPaths = Array.isArray(ingestConfig.extraPaths)
        ? ingestConfig.extraPaths.filter((p: unknown): p is string => typeof p === "string")
        : [];

      const combined = includeDefaultPaths ? [...defaultPaths, ...extraPaths] : extraPaths;
      // Dedupe while keeping order
      const out: string[] = [];
      const seen = new Set<string>();
      for (const p of combined) {
        const key = p.trim();
        if (!key || seen.has(key)) continue;
        seen.add(key);
        out.push(key);
      }
      return out;
    };

    const getPluginConfig = (toolCtx?: { config?: any }) => {
      // Prefer plugin-scoped config (what users edit under plugins.entries["memu-engine"].config)
      if (api.pluginConfig && typeof api.pluginConfig === "object") {
        return api.pluginConfig as Record<string, unknown>;
      }

      // Fallback: derive from full OpenClaw config if present
      const fullCfg = toolCtx?.config;
      const cfgFromFull = fullCfg?.plugins?.entries?.[api.id]?.config;
      if (cfgFromFull && typeof cfgFromFull === "object") {
        return cfgFromFull as Record<string, unknown>;
      }

      return {};
    };
    
    // ---------------------------------------------------------
    // 1. Cross-Platform Background Service
    // ---------------------------------------------------------
    let syncProcess: ChildProcess | null = null;
    let isShuttingDown = false;

    const getUserId = (pluginConfig: any): string => {
      const fromConfig = pluginConfig?.userId;
      if (typeof fromConfig === "string" && fromConfig.trim()) return fromConfig.trim();
      const fromEnv = process.env.MEMU_USER_ID;
      if (typeof fromEnv === "string" && fromEnv.trim()) return fromEnv.trim();
      return "default";
    };

    const getSessionDir = (): string => {
      const fromEnv = process.env.OPENCLAW_SESSIONS_DIR;
      if (fromEnv && fs.existsSync(fromEnv)) return fromEnv;

      const home = process.env.HOME || "";
      const candidates = [
        path.join(home, ".openclaw", "agents", "main", "sessions"),
        path.join(home, ".openclaw", "sessions"),
      ];
      for (const c of candidates) {
        if (c && fs.existsSync(c)) return c;
      }
      return candidates[0];
    };

    const pidFilePath = (workspaceDir: string) =>
      path.join(workspaceDir, "memU", "data", "watch_sync.pid");

    const stopSyncService = (workspaceDir: string) => {
      isShuttingDown = true;

      // Best-effort: stop the in-process handle
      if (syncProcess && syncProcess.pid) {
        try {
          syncProcess.kill();
        } catch {
          // ignore
        }
        syncProcess = null;
      }

      // Best-effort: stop any orphaned watcher from previous plugin instance
      try {
        const pidPath = pidFilePath(workspaceDir);
        if (fs.existsSync(pidPath)) {
          const pidStr = fs.readFileSync(pidPath, "utf-8").trim();
          const pid = Number(pidStr);
          if (Number.isFinite(pid) && pid > 1) {
            try {
              process.kill(pid);
            } catch {
              // ignore
            }
          }
          fs.unlinkSync(pidPath);
        }
      } catch {
        // ignore
      }

      isShuttingDown = false;

    };

    // Best-effort: bind background watcher lifecycle to gateway process lifecycle.
    // OpenClaw doesn't expose a formal plugin shutdown hook yet.
    let lastWorkspaceDirForCleanup: string | null = null;
    let shutdownHooksInstalled = false;
    const installShutdownHooksOnce = () => {
      if (shutdownHooksInstalled) return;
      shutdownHooksInstalled = true;

      const cleanup = () => {
        if (!lastWorkspaceDirForCleanup) return;
        try {
          stopSyncService(lastWorkspaceDirForCleanup);
        } catch {
          // ignore
        }
      };

      process.once("exit", cleanup);
      process.once("SIGINT", () => {
        cleanup();
        process.exit(0);
      });
      process.once("SIGTERM", () => {
        cleanup();
        process.exit(0);
      });
    };

    const startSyncService = (pluginConfig: any, workspaceDir: string) => {
      if (syncProcess) return; // Already running

      lastWorkspaceDirForCleanup = workspaceDir;
      installShutdownHooksOnce();

      stopSyncService(workspaceDir);

      const embeddingConfig = pluginConfig.embedding || {};
      const extractionConfig = pluginConfig.extraction || {};
      const ingestConfig = pluginConfig.ingest || {};

      const extraPaths = computeExtraPaths(pluginConfig, workspaceDir);
      const userId = getUserId(pluginConfig);
      const sessionDir = getSessionDir();
      
      const env = {
        ...process.env,
        PYTHONIOENCODING: "utf-8",
        MEMU_USER_ID: userId,
        MEMU_EMBED_PROVIDER: embeddingConfig.provider || "openai",
        MEMU_EMBED_API_KEY: embeddingConfig.apiKey || process.env.MEMU_EMBED_API_KEY || "",
        MEMU_EMBED_BASE_URL: embeddingConfig.baseUrl || "https://api.openai.com/v1",
        MEMU_EMBED_MODEL: embeddingConfig.model || "text-embedding-3-small",

        MEMU_CHAT_PROVIDER: extractionConfig.provider || "openai",
        MEMU_CHAT_API_KEY: extractionConfig.apiKey || process.env.MEMU_CHAT_API_KEY || "",
        MEMU_CHAT_BASE_URL: extractionConfig.baseUrl || "https://api.openai.com/v1",
        MEMU_CHAT_MODEL: extractionConfig.model || "gpt-4o-mini",

        MEMU_DATA_DIR: path.join(workspaceDir, "memU", "data"),
        MEMU_WORKSPACE_DIR: workspaceDir,
        MEMU_EXTRA_PATHS: JSON.stringify(extraPaths),
        MEMU_OUTPUT_LANG: pluginConfig.language || "auto",
        OPENCLAW_SESSIONS_DIR: sessionDir,
      };

      const scriptPath = path.join(pythonRoot, "watch_sync.py");
      
      console.log(`[memU] Starting background sync service: ${scriptPath}`);
      
      // Launch using uv run
      const proc = spawn("uv", ["run", "--project", pythonRoot, "python", scriptPath], {
        cwd: pythonRoot,
        env,
        stdio: "pipe" // Capture logs
      });

      syncProcess = proc;

      isShuttingDown = false;

      // Write PID file for orphan cleanup
      try {
        const pidPath = pidFilePath(workspaceDir);
        fs.mkdirSync(path.dirname(pidPath), { recursive: true });
        if (syncProcess.pid) fs.writeFileSync(pidPath, String(syncProcess.pid), "utf-8");
      } catch {
        // ignore
      }

      // Redirect logs to Gateway console (with prefix)
      proc.stdout?.on("data", (d) => {
        const lines = d.toString().trim().split("\n");
        lines.forEach((l: string) => console.log(`[memU Sync] ${l}`));
      });
      proc.stderr?.on("data", (d) => console.error(`[memU Sync Error] ${d}`));

      proc.on("close", (code) => {
        // Ignore stale close events from an old process instance.
        if (syncProcess !== proc) return;
        syncProcess = null;
        try {
          const pidPath = pidFilePath(workspaceDir);
          if (fs.existsSync(pidPath)) fs.unlinkSync(pidPath);
        } catch {
          // ignore
        }
        if (!isShuttingDown) {
          console.warn(`[memU] Sync service crashed (code ${code}). Restarting in 5s...`);
          setTimeout(() => startSyncService(pluginConfig, workspaceDir), 5000);
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
    
    const runPython = async (
      scriptName: string,
      args: string[],
      pluginConfig: any,
      workspaceDir: string,
    ): Promise<string> => {
      // Key point: Trigger background service here (lazy singleton)
      startSyncService(pluginConfig, workspaceDir);

      const embeddingConfig = pluginConfig.embedding || {};
      const extractionConfig = pluginConfig.extraction || {};
      const userId = getUserId(pluginConfig);
      
      const env = {
        ...process.env,
        PYTHONIOENCODING: "utf-8",
        MEMU_USER_ID: userId,
        
        MEMU_EMBED_PROVIDER: embeddingConfig.provider || "openai",
        MEMU_EMBED_API_KEY: embeddingConfig.apiKey || "",
        MEMU_EMBED_BASE_URL: embeddingConfig.baseUrl || "https://api.openai.com/v1",
        MEMU_EMBED_MODEL: embeddingConfig.model || "text-embedding-3-small",
        
        MEMU_CHAT_PROVIDER: extractionConfig.provider || "openai",
        MEMU_CHAT_API_KEY: extractionConfig.apiKey || "",
        MEMU_CHAT_BASE_URL: extractionConfig.baseUrl || "https://api.openai.com/v1",
        MEMU_CHAT_MODEL: extractionConfig.model || "gpt-4o-mini",

        MEMU_DATA_DIR: path.join(workspaceDir, "memU", "data"),
        MEMU_WORKSPACE_DIR: workspaceDir,
        MEMU_OUTPUT_LANG: pluginConfig.language || "auto",
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

    const searchSchema = {
      type: "object",
      properties: {
        query: { type: "string", description: "Search query" }
      },
      required: ["query"]
    };

    const getSchema = {
      type: "object",
      properties: {
        path: { type: "string", description: "Path to the memory file or memU resource URL." },
        offset: { type: "integer", description: "Start line (0-based). Only for file paths." },
        limit: { type: "integer", description: "Number of lines to read. Only for file paths." },
      },
      required: ["path"],
    };

    api.registerTool(
      (ctx) => {
        const pluginConfig = getPluginConfig(ctx);
        const workspaceDir = ctx.workspaceDir || process.cwd();

        const searchTool = (name: string, description: string) => ({
          name,
          description,
          parameters: searchSchema,
          async execute(_toolCallId: string, params: unknown) {
            const { query } = params as { query?: string };
            if (!query) {
              return {
                content: [{ type: "text", text: "Missing required parameter: query" }],
                details: { error: "missing_query" },
              };
            }

            const result = await runPython("search.py", [query], pluginConfig, workspaceDir);
            return {
              content: [{ type: "text", text: `--- [memU Retrieval System] ---\n${result}` }],
              details: { query },
            };
          },
        });

        const getTool = (name: string, description: string) => ({
          name,
          description,
          parameters: getSchema,
          async execute(_toolCallId: string, params: unknown) {
            const { path: memoryPath, offset, limit } = params as {
              path?: string;
              offset?: number;
              limit?: number;
            };
            if (!memoryPath) {
              return {
                content: [{ type: "text", text: "Missing required parameter: path" }],
                details: { error: "missing_path" },
              };
            }

            const args: string[] = [memoryPath];
            if (typeof offset === "number" && Number.isFinite(offset)) {
              args.push("--offset", String(Math.trunc(offset)));
            }
            if (typeof limit === "number" && Number.isFinite(limit)) {
              args.push("--limit", String(Math.trunc(limit)));
            }

            const result = await runPython("get.py", args, pluginConfig, workspaceDir);
            return {
              content: [{ type: "text", text: result }],
              details: { path: memoryPath },
            };
          },
        });

        return [
          searchTool("memu_search", "Agentic semantic search on the memU long-term database."),
          searchTool("memory_search", "Mandatory recall step: semantically search the memory system."),
          getTool("memu_get", "Retrieve content from memU database or workspace disk."),
          getTool("memory_get", "Read a specific memory Markdown file."),
        ];
      },
      { names: ["memu_search", "memory_search", "memu_get", "memory_get"] },
    );
  }
};

export default memuEnginePlugin;
