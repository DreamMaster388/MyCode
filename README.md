# MyCode

基于开源框架 **HelloAgents** 二次开发的本地命令行编码助手（Coding Agent）。
在框架的智能体、上下文工程与工具系统之上，本项目自行实现了编码智能体主循环、
三模式安全沙箱、流式交互、CLI 命令体系与 SWE-bench 风格评测框架。

- 框架来源：HelloAgents（https://github.com/jjyaoao/HelloAgents）
- 作者 / 维护者：jjyaoao（https://github.com/jjyaoao）

---

## 目录

- [特性](#特性)
- [快速开始](#快速开始)
- [使用方式](#使用方式)
- [安全模型](#安全模型)
- [框架与自研代码归属](#框架与自研代码归属)
- [目录结构](#目录结构)
- [评测](#评测)
- [测试](#测试)
- [环境变量](#环境变量)
- [开发约定](#开发约定)
- [项目来源与致谢](#项目来源与致谢)
- [许可证](#许可证)

---

## 特性

- **编码智能体主循环**：`CodeAgent` 基于 Function Calling，支持流式思考/正文输出、
  工具调用与结果摘要、步数与 token 预算控制，并在预算耗尽时收敛为纯文本结论。
- **三运行模式**：`plan` / `build` / `trusted` 决定沙箱策略——`plan` 只读
  （写入被内核拒绝），`build` 可写项目目录与额外可写根，`trusted` 全盘可读、
  写仍限项目目录与批准目录且不审批。
- **读写沙箱**：以 `realpath` 计算文件身份，文件工具的写操作必须落在可写根之内；
  bash 另由内核围栏强制读写边界（系统/项目/批准目录可读，工作区/批准目录可写）。
- **专用工具优先**：提供 `Read / Write / Edit / Bash / Grep / Glob`，
  系统提示词要求优先使用专用工具、仅在必要时使用 Bash。
- **CLI 命令体系**：以 `/` 前缀识别命令（如 `/mode plan`、`/mode build`、
  `/mode trusted`），在提交给 LLM 前拦截。
- **可观测性**：将每轮模型输出、工具调用与结果记录到 `memory/traces/`。
- **本地评测**：SWE-bench 风格 harness，在隔离仓库中运行 agent、提取补丁、
  跑测试并按 FAIL_TO_PASS / PASS_TO_PASS 判定是否解决。

---

## 快速开始

要求 Python 3.10+。

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置环境变量
cp .env.example .env
#   编辑 .env，至少填入 LLM_API_KEY，按需修改 LLM_MODEL_ID / LLM_BASE_URL

# 3. 启动交互式编码助手
python main.py
# 或
python -m agents
```

启动后为多轮交互式会话，输入 `exit` 或 `quit` 退出，`Ctrl+C` 中断。

---

## 使用方式

### 交互示例

```
(build) 你> 帮我看看 agents/core/mode.py 里 plan 模式的沙箱策略
...
(build) 你> /mode plan
(plan)  你> 阅读 agents/fs/policy.py 并总结写授权逻辑
```

- 括号内为当前模式（`build` / `plan` / `trusted`）。
- 思考过程以 `🧠` 前缀流式打印，正文逐字输出。
- 每次工具调用先打印 `Executing tool: <name>`，再打印结果摘要。

### 内置命令

| 命令 | 说明 |
| --- | --- |
| `/mode plan` | 切换到 plan（只读）模式 |
| `/mode build` | 切换到 build（可写）模式 |
| `/mode trusted` | 切换到 trusted（全盘可读、写仍受限、不审批）模式 |
| `/allow` | 列出当前读/写批准目录 |
| `/allow read <path>` | 添加可读批准目录（相对项目根解析） |
| `/allow write <path>` | 添加可写批准目录 |
| `/allow remove read\|write <path>` | 移除可读/可写批准目录 |
| `exit` / `quit` | 退出 |
| `Ctrl+C` | 中断当前会话 |

命令在提交给 LLM 之前被 `CommandRegistry` 拦截，未识别为命令的输入才作为对话内容。

---

## 安全模型

核心原则：**默认拒绝**。一次调用真正能读写的范围由 `SandboxPolicy` 决定；策略之外的
访问由专用工具审批或内核围栏拦截；沙箱后端不可用时拒绝执行，而不是退回无围栏执行。

### 1. 三种模式

`agents/core/mode.py` 的 `ModeGuard` 持有当前模式、基础策略与用户批准目录，
`sandbox_policy()` 把模式折算成 `SandboxPolicy` 交给 bash 内核围栏：

| 模式 | 专用工具可读 | 可写 | 专用工具审批 | bash |
| --- | --- | --- | --- | --- |
| `plan` | 项目目录 + 批准目录 | 无 | 外部读需审批；写直接拒绝 | 内核围栏，不审批 |
| `build` | 项目目录 + 批准目录 | 项目目录 + 批准目录 | 外部读/写需审批 | 内核围栏，不审批 |
| `trusted` | 全盘 | 项目目录 + 批准目录 | 不审批；越界写直接拒绝 | 全盘读、写受限，不审批 |

- 表中的“专用工具可读”不含系统读根；系统读根只服务于 bash 命令执行。
- `trusted` 映射为 `workspace-write + read_all`，不会映射成 `danger-full-access`，
  写边界仍受项目目录与批准目录限制。
- 用 `/mode plan|build|trusted` 切换；批准目录跨模式保留。

### 2. 专用工具审批

`Read / Grep / Glob / Write / Edit / MultiEdit` 在访问可读/可写根之外的路径时，
会通过 `ModeGuard.request_access()` 请求用户决定：

- `o` 本次（ONCE）：只放行当前工具调用，不修改策略；写操作在本次调用内的临时文件
  与 `.backups` 派生路径沿用同一临时许可，调用结束即丢弃。
- `a` 永久（ALWAYS）：把目标归一为目录（目录取自身，文件取父目录）加入批准目录；
  后续专用工具与 bash 通过共享的 `sandbox_policy()` 立即获得新根。
- `d` 拒绝（DENY）：返回 `[sandbox: approval denied]`，不执行、不落盘。
- 默认 `DenyAllHandler` 无交互 fail-closed；`plan` 的写与 `trusted` 的所有越界访问
  不进入审批，直接拒绝。
- `/allow` 查看批准目录；`/allow read <path>`、`/allow write <path>` 添加；
  `/allow remove read|write <path>` 移除。路径相对项目根解析。
- 永久批准目前只保存在当前会话内存中，不跨进程持久化。

### 3. 文件系统策略

- `agents/fs/target.py`：`resolve_path()` 把路径解析为稳定身份 `FsTarget`
  （`target_key` 用 `os.path.realpath`），`is_within()` 按身份判断父子关系。
- `agents/fs/policy.py`：
  - `SandboxMode`：`read-only` / `workspace-write` / `danger-full-access`；
  - `SandboxPolicy`：工作区根 + 额外可写根 + 额外可读根 + `read_all`；
  - `writable_roots()` / `readable_roots()`：可写根 / 可读根（`read_all` 时为 `/`）；
  - `evaluate_read()` / `evaluate_write()`：返回 `ALLOW` / `ASK` / `DENY`；
  - `authorize_read()` / `authorize_write()`：策略内的严格封装，越界抛
    `SandboxApprovalRequired` / `SandboxDenied`。
- 文件工具通过基类元数据 `access_kind`（`read`/`write`）与 `path_params`
  （`agents/tools/base.py`）声明路径访问方式；`fs_access.py` 统一执行授权与审批。
- 文件工具的读边界是**项目目录 + 用户批准目录**；系统读根（`/usr`、`/dev` 等）
  只进入 bash 的内核围栏，不放开给 Python 文件工具。

### 4. 内核读写围栏

`agents/sandbox/` 提供**通用受限命令包装**（`SandboxProvider.wrap(argv)`，当前由
Bash 工具使用）：任意 argv 被包装后执行，读/写位置由内核保证，与命令如何书写无关。

- 后端：`LandlockBackend`（ctypes 启动器 `landlock_exec.py`，Linux 基线）与
  `BwrapBackend`（挂载 / PID 命名空间，可选）；`SandboxProvider` 按
  `landlock -> bwrap` 选首个可用后端，**全部不可用则 fail-closed 拒绝执行**。
- Landlock 提供读写围栏；降级到 bwrap 时 bwrap 仍 `--ro-bind / /`，只有写围栏，
  **读围栏不可用**。需要读限制时应使用 Landlock。
- 模式映射：`plan -> read-only`；`build -> workspace-write`；`trusted -> workspace-write
  + read_all`；`danger-full-access` 不包装。
- 读与写都接管：`--read-root` 对目录授予 `read_file` / `read_dir`，对文件只授予
  `read_file`；`--write-root` 额外授予 `write/remove/make/refer/truncate`
  （写根也可读）；`execute` 不接管。未被任何规则授予的读/写由内核拒绝。
- 读根 = `SYSTEM_READ_ROOTS`（`/usr` 与兼容别名、`/dev`、`/etc/ssl`、`/etc` 下
  必要文件、当前解释器前缀）+ 项目目录 + 用户批准目录；不再整目录放开 `/etc`、
  `/proc`、`/sys`。
- Landlock 规则对子进程继续生效，天然覆盖 `env` / `xargs` / `sh -c` / `$(...)` /
  重定向等所有派生写法。
- 拒绝识别：非零退出且 stderr 命中方言时返回
  `[sandbox: file access denied under <mode> mode]`；脚本采用 `close_fds=True`，
  避免父进程已打开的可写 fd 被继承而绕过围栏。

**范围与已知边界**：只保证 **FS 读/写**；目前只有 Bash 工具接入内核 argv 包装，
Python 文件工具走应用层策略 + 审批；bwrap 降级时读围栏不可用；网络与资源耗尽
不在本期范围。

---

## 框架与自研代码归属

首次框架提交为 `40d5ba6 "引入框架"`，从开源 **HelloAgents** 整体引入框架代码；
此后新增与修改的代码均为本项目自行开发。下表中的“框架原生”指该提交引入、“自研”
指该提交之后新增或改造。

### 一、框架原生（`40d5ba6` 引入）

| 模块 | 内容 |
| --- | --- |
| `agents/core/` | `agent.py`、`config.py`、`exceptions.py`、`lifecycle.py`、`llm.py`、`llm_adapters.py`、`llm_error.py`、`llm_response.py`、`message.py`、`session_store.py`、`streaming.py` |
| `agents/agent/` | `simple_agent.py`、`react_agent.py`、`reflection_agent.py`、`plan_solve_agent.py` |
| `agents/context/` | `builder.py`、`history.py`、`truncator.py` |
| `agents/observability/` | `trace_logger.py` |
| `agents/skill/` | `loader.py` |
| `agents/tools/` | `base.py`、`registry.py`、`response.py`、`errors.py`、`circuit_breaker.py`、`tool_filter.py` |
| `agents/tools/builtin/` | `calculator.py`、`devlog_tool.py`、`file_tools.py`、`skill_tool.py`、`task_tool.py`、`todowrite_tool.py` |
| 其他 | `agents/__init__.py`、`agents/__main__.py`、`main.py`（骨架）、`.env.example`、`requirements.txt` 基础依赖 |

### 二、自行开发（框架引入后新增）

| 模块 | 说明 |
| --- | --- |
| `agents/agent/code_agent.py` | 编码智能体主循环，流式 `stream_run`，工具执行/输出截断/步数与 token 预算/收尾兜底 |
| `agents/core/mode.py` | `AgentMode`（build/plan/trusted）与 `ModeGuard`（模式到 `SandboxPolicy` 的映射、审批入口） |
| `agents/fs/target.py` | 路径解析与身份判定 seam（`FsTarget`/`resolve_path`/`is_within`） |
| `agents/fs/policy.py` | 沙箱模式、策略与读写授权判定（`SandboxPolicy`/`authorize_read`/`authorize_write`） |
| `agents/fs/__init__.py` | 文件系统子包导出 |
| `agents/sandbox/` | 内核读写围栏：`SandboxProvider.wrap(argv)` + `LandlockBackend`/`BwrapBackend` + `landlock_exec.py` 启动器 |
| `agents/cli/base.py` | CLI 命令抽象基类 `Command` |
| `agents/cli/registry.py` | 命令注册表 `CommandRegistry`，`/` 前缀识别 |
| `agents/cli/parsing.py` | CLI 文本解析（命令名与参数） |
| `agents/cli/builtin/mode_command.py` | `/mode` 命令实现 |
| `agents/cli/builtin/allow_command.py` | `/allow` 批准目录查看与增删 |
| `agents/tools/builtin/bash_tool.py` | Bash 执行工具（绑定工作目录与 policy） |
| `agents/tools/builtin/grep_tool.py` | Grep 内容搜索工具 |
| `agents/tools/builtin/glob_tool.py` | Glob 文件查找工具 |
| `eval/` | SWE-bench 风格评测框架 |
| `test/` | 单元测试（模式、沙箱策略、内核围栏等） |

### 三、在框架文件上的自研改动

| 文件 | 改动内容 |
| --- | --- |
| `agents/core/agent.py` | 接入 `ModeGuard`，用于模式切换与工具共享策略 |
| `agents/core/llm.py` | 新增带工具调用的流式接口 `stream_invoke_with_tools` |
| `agents/core/llm_response.py` | 新增流式 chunk 类型与 `ToolCall` 结构 |
| `agents/core/llm_adapters.py` | 适配流式工具调用 |
| `agents/tools/base.py` | 新增能力元数据 `access_kind` / `path_params`，新增 `brief()` 结果摘要 |
| `agents/tools/builtin/file_tools.py` | `Read/Write/Edit` 接入路径 policy 与沙箱鉴权 |
| `agents/tools/builtin/__init__.py` | 导出新增工具 |
| `main.py` | 改为消费 `stream_run` 的流式交互入口，注册命令与守卫 |

---

## 目录结构

```
MyCode/
├── main.py                     # 交互式入口
├── requirements.txt
├── .env.example                # 环境变量示例
├── agents/
│   ├── agent/                  # 智能体：simple/react/reflection/plan_solve（框架）、code_agent（自研）
│   ├── cli/                    # 自研：CLI 命令体系
│   ├── context/                # 上下文构建/历史（含 token 计量）/截断（框架）
│   ├── core/                   # LLM、Agent 基类、流式、配置（框架）+ mode（自研）
│   ├── fs/                     # 自研：路径解析与沙箱策略
│   ├── sandbox/                # 自研：内核读写围栏后端（Landlock/bwrap）
│   ├── observability/          # trace 日志（框架）
│   ├── skill/                  # 技能加载（框架）
│   └── tools/                  # 工具基类/注册表（框架）+ Bash/Grep/Glob（自研）
├── eval/                       # 自研：SWE-bench 风格评测
│   ├── instances/              # 评测用例（含合成仓库）
│   ├── results/                # 结果输出
│   ├── harness.py              # 单实例编排
│   ├── agent_runner.py         # 运行 CodeAgent
│   ├── judge.py                # 测试结果解析与判定
│   └── run_eval.py             # 批量运行 CLI
├── test/                       # 自研：pytest 单测
│   ├── core/test_mode.py
│   ├── fs/test_policy.py
│   └── sandbox/                # 内核写围栏测试
├── memory/traces/              # 运行期 trace 产物
├── log/                        # 日志
└── tool-output/                # 工具完整输出（超长时）
```

---

## 评测

**流程**：建隔离仓库 → 运行 agent → 提取 git diff 作为模型补丁 →
写入 golden 测试 → 运行 pytest → 解析结果并判定。

**判定规则**（`eval/judge.py`）：仅当所有 `FAIL_TO_PASS` 测试通过、且所有
`PASS_TO_PASS` 测试未回归时，实例判定为 `RESOLVED`。

```bash
# 运行全部实例（注意用 -m，脚本内含相对导入）
python -m eval.run_eval --instances eval/instances --out eval/results

# 只跑前 1 个
python -m eval.run_eval --instances eval/instances --out eval/results --limit 1

# 指定 agent 最大步数
python -m eval.run_eval --max-steps 25
```

**实例配置字段**（`eval/instances/*.json`）：

| 字段 | 说明 |
| --- | --- |
| `instance_id` | 实例唯一标识 |
| `problem_statement` | 交给 agent 的问题描述 |
| `repo_url` / `base_commit` | 远程仓库地址与基线提交（可选） |
| `repo_dir` | 本地合成仓库相对路径（无 `repo_url` 时使用） |
| `test_patch` / `test_files` | golden 测试补丁或测试文件内容 |
| `test_cmd` | 运行测试的命令 |
| `fail_to_pass` | 修复后必须通过的测试 |
| `pass_to_pass` | 修复后不得回归的测试 |

结果写入 `eval/results/results.jsonl`，模型补丁保存于 `eval/results/patches/`。

---

## 测试

```bash
python -m pytest test/
```

当前包含：

- `test/core/test_mode.py`：AgentMode 取值、默认策略构造与模式切换；
- `test/context/test_history.py`：HistoryManager 的 id 分配、轮次统计、摘要快照、上下文构建、压缩判断与摘要生成；
- `test/fs/test_policy.py`：沙箱策略与可写根；
- `test/sandbox/`：Landlock 启动器、后端选择、沙箱读/写根与 bash 内核围栏集成。

---

## 环境变量

复制 `.env.example` 为 `.env` 后按需填写。

| 变量 | 必填 | 说明 |
| --- | --- | --- |
| `LLM_MODEL_ID` | 是 | 模型 ID，如 `deepseek-chat` |
| `LLM_API_KEY` | 是 | API Key |
| `LLM_BASE_URL` | 是 | 兼容 OpenAI 格式的服务地址 |
| `LLM_TIMEOUT` | 否 | 请求超时（秒），默认 60 |
| `SERPAPI_API_KEY` | 否 | 联网搜索（预留） |
| `QDRANT_*` | 否 | 向量数据库（记忆/RAG，预留） |
| `EMBED_*` | 否 | Embedding 模型（预留） |
| `GITHUB_PERSONAL_ACCESS_TOKEN` / `HF_TOKEN` | 否 | 预留 |

兼容所有 OpenAI 格式接口（DeepSeek / Qwen / Kimi / 智谱 / Ollama 等）。

---

## 开发约定

- 提交信息采用 `<type>: <描述>` 前缀，已有类型：`feat`、`fix`、`test`、`refactor`。
- 修改框架文件时应在提交信息中说明动因（如“接入 ModeGuard”“接入 seam”），
  保持框架改动与自研代码可追溯。
- 安全相关改动遵循默认拒绝原则：无法解析、无法判断的情况一律拦截而非放行。

---

## 项目来源与致谢

本项目的框架底座为开源项目 **HelloAgents**，特此致谢。

- 项目地址：https://github.com/jjyaoao/HelloAgents
- 作者 / 维护者：jjyaoao
- 镜像仓库：https://atomgit.com/jjyaoao/HelloAgents
- 关联教程：Hello-Agents（https://github.com/datawhalechina/hello-agents）
- 引入提交：`40d5ba6 "引入框架"`

---

## 许可证

本项目是 HelloAgents 的衍生作品，遵循其 **CC BY-NC-SA 4.0**
（署名—非商业性使用—相同方式共享 4.0 国际）许可证，完整条款见
仓库根目录 [LICENSE](LICENSE)。

- 框架原始许可证：https://github.com/jjyaoao/HelloAgents/blob/main/LICENSE
- 许可证要点：
  - 署名（Attribution）：需注明原作者；
  - 相同方式共享（ShareAlike）：修改后的作品须使用相同许可证；
  - 非商业性使用（NonCommercial）：不得用于商业目的。
- 本项目新增代码同样采用 CC BY-NC-SA 4.0 发布。
- 如需商业使用，请联系原作者获取授权。
