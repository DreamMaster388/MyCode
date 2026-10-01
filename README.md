# MyCode

基于开源框架 **HelloAgents** 二次开发的本地命令行编码助手（Coding Agent）。
在框架的智能体、上下文工程与工具系统之上，本项目自行实现了编码智能体主循环、
双模式安全沙箱、bash 命令审计、流式交互、CLI 命令体系与 SWE-bench 风格评测框架。

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
- **双运行模式（硬约束）**：
  - `plan`：只读探索代码，白名单外的一律拦截；
  - `build`：允许写文件与执行构建命令，但仍受高危拦截与路径沙箱约束。
- **bash 命令审计**：基于 `bashlex` 解析命令 AST，将管道、重定向、子命令、
  控制流、命令替换等归一化为审计模型，解析失败即默认拒绝。
- **写路径沙箱**：以 `realpath` 计算文件身份，写操作必须落在可写根之内；
  文件工具与 bash 写重定向共用同一套策略。
- **专用工具优先**：提供 `Read / Write / Edit / Bash / Grep / Glob`，
  系统提示词要求优先使用专用工具、仅在必要时使用 Bash。
- **CLI 命令体系**：以 `/` 前缀识别命令（如 `/mode plan`、`/mode build`），
  在提交给 LLM 前拦截。
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
(build) 你> 帮我看看 agents/core/mode.py 里 plan 模式都拦截了哪些命令
...
(build) 你> /mode plan
(plan)  你> 阅读 agents/fs/policy.py 并总结写授权逻辑
```

- 括号内为当前模式（`build` / `plan`）。
- 思考过程以 `🧠` 前缀流式打印，正文逐字输出。
- 每次工具调用先打印 `Executing tool: <name>`，再打印结果摘要。

### 内置命令

| 命令 | 说明 |
| --- | --- |
| `/mode plan` | 切换到 plan（只读）模式 |
| `/mode build` | 切换到 build（可写）模式 |
| `exit` / `quit` | 退出 |
| `Ctrl+C` | 中断当前会话 |

命令在提交给 LLM 之前被 `CommandRegistry` 拦截，未识别为命令的输入才作为对话内容。

---

## 安全模型

核心原则：**默认拒绝**。

### 1. 执行入口守卫 `ModeGuard`

所有工具调用在执行前必须经过 `agents/core/mode.py` 的 `ModeGuard.check_tool()`：
返回 `None` 放行，返回字符串表示拦截原因（作为错误反馈给模型）。

- **plan 模式**
  - 非 bash 工具：仅放行只读白名单（`Read / Grep / Glob` 等）内的工具。
  - bash：整棵命令树必须“全只读”才放行；控制流（`if/for/while`）直接拒绝；
    写重定向与提权/下载/安装等风险标记均拒绝；对 `$()` 与子 shell 递归审查，
    防止 `ls $(rm -rf /)` 这类“外层只读、内层破坏”的绕过。
  - 解释器（`python/node/bash/...`）即使命令名看似无害也一律拒绝。
- **build 模式**
  - 工具：写工具对每个路径参数做写路径沙箱校验。
  - bash：拦截高危命令（提权 `privilege`、破坏性 `footgun`、任意下载 `download`），
    允许依赖安装（`pkg_install`），并对写重定向目标做路径沙箱校验。

### 2. 命令审计 `CommandAudit`

`agents/core/command_audit.py` 使用 `bashlex` 把命令解析为归一化模型：

- `Segment`：单条命令（命令名、参数、重定向、风险标记）；
- `Redirection`：重定向操作符与目标，区分读/写；
- `nested_commands`：`$()` / 子 shell 中的子命令，递归审计；
- `has_control_flow` / `has_pipeline` / `has_subshell` 等整体标志；
- 风险标记：`privilege`（sudo/su/doas）、`download`（curl/wget）、
  `footgun`（rm/dd/mkfs/shred、git 破坏性子命令）、`pkg_install`（pip/npm/make/...）。

任何解析失败都返回非空 `parse_error`，由规则层默认拒绝。

### 3. 文件系统沙箱与策略

- `agents/fs/target.py`：路径解析 seam。`resolve_path()` 把路径解析为稳定身份
  `FsTarget`（`target_key` 用 `os.path.realpath`），`is_within()` 按身份判断父子关系。
- `agents/fs/policy.py`：
  - `SandboxMode`：`read-only` / `workspace-write` / `danger-full-access`；
  - `SandboxPolicy`：工作区根 + 额外可写根；
  - `authorize_write()`：放行返回 `None`，拒绝抛 `SandboxDenied`。
- 文件工具与 bash 写重定向共用同一 policy。

工具通过基类能力元数据 `is_write_tool` 与 `path_params`
（`agents/tools/base.py`）声明自身是否为写工具、哪些参数是路径。

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
| `agents/context/` | `builder.py`、`history.py`、`token_counter.py`、`truncator.py` |
| `agents/observability/` | `trace_logger.py` |
| `agents/skill/` | `loader.py` |
| `agents/tools/` | `base.py`、`registry.py`、`response.py`、`errors.py`、`circuit_breaker.py`、`tool_filter.py` |
| `agents/tools/builtin/` | `calculator.py`、`devlog_tool.py`、`file_tools.py`、`skill_tool.py`、`task_tool.py`、`todowrite_tool.py` |
| 其他 | `agents/__init__.py`、`agents/__main__.py`、`main.py`（骨架）、`.env.example`、`requirements.txt` 基础依赖 |

### 二、自行开发（框架引入后新增）

| 模块 | 说明 |
| --- | --- |
| `agents/agent/code_agent.py` | 编码智能体主循环，流式 `stream_run`，工具执行/输出截断/步数与 token 预算/收尾兜底 |
| `agents/core/mode.py` | `AgentMode`（build/plan）与执行入口守卫 `ModeGuard` 及规则函数 |
| `agents/core/command_audit.py` | 基于 `bashlex` 的 bash 命令审计核心 |
| `agents/fs/target.py` | 路径解析与身份判定 seam（`FsTarget`/`resolve_path`/`is_within`） |
| `agents/fs/policy.py` | 沙箱模式、策略与写授权判定（`SandboxPolicy`/`authorize_write`） |
| `agents/fs/__init__.py` | 文件系统子包导出 |
| `agents/cli/base.py` | CLI 命令抽象基类 `Command` |
| `agents/cli/registry.py` | 命令注册表 `CommandRegistry`，`/` 前缀识别 |
| `agents/cli/builtin/mode_command.py` | `/mode` 命令实现 |
| `agents/tools/builtin/bash_tool.py` | Bash 执行工具（绑定工作目录与 policy） |
| `agents/tools/builtin/grep_tool.py` | Grep 内容搜索工具 |
| `agents/tools/builtin/glob_tool.py` | Glob 文件查找工具 |
| `eval/` | SWE-bench 风格评测框架 |
| `test/` | 单元测试（命令审计、沙箱策略等） |

### 三、在框架文件上的自研改动

| 文件 | 改动内容 |
| --- | --- |
| `agents/core/agent.py` | 接入 `ModeGuard`，工具执行前统一鉴权 |
| `agents/core/llm.py` | 新增带工具调用的流式接口 `stream_invoke_with_tools` |
| `agents/core/llm_response.py` | 新增流式 chunk 类型与 `ToolCall` 结构 |
| `agents/core/llm_adapters.py` | 适配流式工具调用 |
| `agents/tools/base.py` | 新增能力元数据 `is_write_tool` / `path_params`，新增 `brief()` 结果摘要 |
| `agents/tools/builtin/file_tools.py` | `Read/Write/Edit` 接入路径 policy 与沙箱鉴权 |
| `agents/tools/builtin/__init__.py` | 导出新增工具 |
| `main.py` | 改为消费 `stream_run` 的流式交互入口，注册命令与守卫 |
| `requirements.txt` | 新增 `bashlex` 依赖 |

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
│   ├── context/                # 上下文构建/历史/token 计数/截断（框架）
│   ├── core/                   # LLM、Agent 基类、流式、配置（框架）+ mode/command_audit（自研）
│   ├── fs/                     # 自研：路径解析与沙箱策略
│   ├── observability/          # trace 日志（框架）
│   ├── skill/                  # 技能加载（框架）
│   └── tools/                  # 工具基类/注册表（框架）+ bash/grep/glob（自研）
├── eval/                       # 自研：SWE-bench 风格评测
│   ├── instances/              # 评测用例（含合成仓库）
│   ├── results/                # 结果输出
│   ├── harness.py              # 单实例编排
│   ├── agent_runner.py         # 运行 CodeAgent
│   ├── judge.py                # 测试结果解析与判定
│   └── run_eval.py             # 批量运行 CLI
├── test/                       # 自研：pytest 单测
│   ├── core/test_command_audit.py
│   └── fs/test_policy.py
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
# 运行全部实例
python eval/run_eval.py --instances eval/instances --out eval/results

# 只跑前 1 个
python eval/run_eval.py --instances eval/instances --out eval/results --limit 1

# 指定 agent 最大步数
python eval/run_eval.py --max-steps 25
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
pytest test/
```

当前包含：

- `test/core/test_command_audit.py`：命令审计、风险标记、写重定向判定；
- `test/fs/test_policy.py`：沙箱策略与可写根。

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
