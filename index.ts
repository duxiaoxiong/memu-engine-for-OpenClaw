import type { OpenClawPluginApi, ToolDefinition } from "openclaw/plugin-sdk";
import { spawn, type ChildProcess } from "node:child_process";
import path from "node:path";

const memuEnginePlugin = {
  id: "memu-engine",
  name: "memU Agentic Engine",
  kind: "memory",

  register(api: OpenClawPluginApi) {
    const pythonRoot = path.join(__dirname, "python");
    // 从上下文获取配置，或者使用环境变量/默认值
    // 注意：api.config 在 register 阶段可能还未完全解析，建议在工具调用时动态获取，
    // 但对于后台服务，我们需要一个初始化配置。
    // 这里简化处理：我们假设用户已经在 openclaw.json 配好了 key
    // 或者我们从 process.env 里读（如果用户配了全局 env）
    
    // ---------------------------------------------------------
    // 1. 跨平台后台服务 (Cross-Platform Background Service)
    // ---------------------------------------------------------
    let syncProcess: ChildProcess | null = null;
    let isShuttingDown = false;

    const startSyncService = (config: any, workspaceDir: string) => {
      if (syncProcess) return; // 已经在运行

      const env = {
        ...process.env,
        PYTHONIOENCODING: "utf-8",
        SILICONFLOW_API_KEY: config.embedding?.apiKey || process.env.SILICONFLOW_API_KEY || "",
        MEMU_EMBED_BASE_URL: config.embedding?.baseUrl || "https://api.siliconflow.cn/v1",
        MEMU_EMBED_MODEL: config.embedding?.model || "BAAI/bge-m3",
        MEMU_DATA_DIR: path.join(workspaceDir, "memU", "data"),
        // 自动推断 session 目录：通常在 workspace 同级的 agents/main/sessions
        // 但这里我们假设标准目录结构
        OPENCLAW_SESSIONS_DIR: path.join(process.env.HOME || "", ".openclaw/agents/main/sessions")
      };

      const scriptPath = path.join(pythonRoot, "watch_sync.py");
      
      console.log(`[memU] Starting background sync service: ${scriptPath}`);
      
      // 使用 uv run 启动
      syncProcess = spawn("uv", ["run", "--project", pythonRoot, "python", scriptPath], {
        cwd: pythonRoot,
        env,
        stdio: "pipe" // 捕获日志
      });

      // 日志重定向到 Gateway 控制台 (带前缀)
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

    // 利用一个空的 "init" 工具或者 hook 来触发服务启动
    // OpenClaw 目前没有显式的 onStart hook 暴露给插件，
    // 但我们可以利用 registerService (如果有) 或者懒加载机制。
    // 这里我们用一个立即执行的副作用，但需要获取 config。
    // 权衡之下，我们在 register 阶段无法获得完整的 workspaceDir，
    // 所以我们把启动逻辑绑定到第一次工具调用，或者等待 OpenClaw 的初始化信号。
    
    // *改进*：我们注册一个内部 hook，或者在第一次 memory_search 时懒加载启动。
    // 为了稳健，我们先用懒加载模式。
    
    // ---------------------------------------------------------
    // 2. 工具注册 (Tools)
    // ---------------------------------------------------------
    
    const runPython = async (scriptName: string, args: string[], config: any, workspaceDir: string): Promise<string> => {
      // ** 关键点：在这里触发后台服务 (懒加载单例) **
      startSyncService(config, workspaceDir);

      const env = {
        ...process.env,
        PYTHONIOENCODING: "utf-8",
        SILICONFLOW_API_KEY: config.embedding?.apiKey || "",
        MEMU_EMBED_BASE_URL: config.embedding?.baseUrl || "https://api.siliconflow.cn/v1",
        MEMU_EMBED_MODEL: config.embedding?.model || "BAAI/bge-m3",
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
      properties: { query: { type: "string" }, maxResults: { type: "number" }, minScore: { type: "number" } },
      required: ["query"]
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
      properties: { path: { type: "string", description: "Path to the memory file or memU resource URL." } },
      required: ["path"]
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
