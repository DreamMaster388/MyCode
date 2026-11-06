# MyCode

MyCode 是一个运行在本地终端中的编码智能体（Coding Agent）。它会读取仓库、定位问题、修改文件、运行测试，并根据测试结果继续下一轮，而不是只给出一段修改建议。

项目基于开源框架 **HelloAgents** 二次开发。框架提供了 LLM 客户端、Agent 基类、上下文与工具注册等基础设施；本项目在此基础上重点补齐了四件事：

- 一个可落地的多步编码智能体主循环；
- 专用工具优先、Bash 兜底的工具体系；
- 应用层审批 + 内核围栏的双层安全边界；
- 可复现的 SWE-bench 风格评测闭环。

---

## 目录

- [核心设计](#核心设计)
  - [1. 基础编码智能体架构](#1-基础编码智能体架构)
  - [2. 双层安全模型](#2-双层安全模型)
  - [3. 上下文管理体系](#3-上下文管理体系)
  - [4. 评测系统](#4-评测系统)
- [快速开始](#快速开始)
- [使用方式](#使用方式)
- [项目结构](#项目结构)
- [测试](#测试)
- [环境变量](#环境变量)
- [框架与本项目的边界](#框架与本项目的边界)
- [来源与许可](#来源与许可)

---

## 核心设计

### 1. 基础编码智能体架构

编码任务不是一次对话，而是一个不断收敛的循环：读代码、做判断、调用工具、观察结果，再决定下一步。

**主循环**

`CodeAgent` 基于 Function Calling 实现多步循环：

1. 把系统提示词、历史上下文和工具 Schema 发给模型；
2. 模型返回正文与工具调用；
3. 顺序串行执行工具，把结果写回历史；
4. 没有新的工具调用时自然结束；否则进入下一轮。

循环支持最大步数与 token 预算约束。触发上限时，会强制模型收起工具调用并输出纯文本结论，避免任务无限运行，也避免最终只留下一个半成品。

**工具体系**

编码智能体需要两类能力：精确的文件操作，以及执行外部命令。

- 专用工具负责后者之外的大部分工作：`Read`、`Write`、`Edit`、`Grep`、`Glob`；
- `Bash` 负责构建、测试、安装依赖和环境检查；
- 工具通过统一注册表暴露 Schema，模型按 Function Calling 协议调用；
- 系统提示词要求“优先专用工具、仅在必要时使用 Bash”，减少用 `cat`/`sed` 模拟文件操作带来的不可控性。

工具输出还会经过统一截断：超长结果只把预览返回给模型，完整内容落盘保留。这样既不撑爆上下文，也不丢排查信息。

**命令体系**

终端交互层在输入进入模型之前先识别 `/` 命令：

- `/mode` 切换运行模式；
- `/allow` 查看或增删用户批准目录；
- 未识别的输入才作为对话内容发送给模型。

因此模式切换、授权目录调整这类操作不会消耗模型调用，也不会污染对话历史。

```text
用户输入
   │
   ├── /mode、/allow 等命令 ── 本地处理
   │
   └── 普通任务
          │
          ▼
   LLM（流式 Function Calling）
          │
          ├── 无工具调用 ── 输出最终结论
          │
          └── 有工具调用
                  │
                  ▼
          工具执行 + 结果截断
                  │
                  ▼
          写回历史，进入下一轮
```

### 2. 双层安全模型

编码智能体和聊天机器人最大的区别是：它会真的执行命令、真的改文件。安全边界不能只靠提示词。

MyCode 把安全拆成两层：**应用层决定“能不能做”**，**内核层保证“只能做到这里”**。

#### 第一层：模式、路径策略与审批

三种运行模式覆盖不同的工作场景：

| 模式 | 专用工具可读 | 可写 | 专用工具审批 |
| --- | --- | --- | --- |
| `plan` | 项目目录 + 批准目录 | 无 | 外部读需审批；写直接拒绝 |
| `build` | 项目目录 + 批准目录 | 项目目录 + 批准目录 | 外部读/写需审批 |
| `trusted` | 全盘 | 项目目录 + 批准目录 | 不审批；越界写直接拒绝 |

- `plan` 用于只读探索和方案设计，从策略上就不可能修改文件；
- `build` 是日常编码模式，项目目录内直接读写，项目外按需申请；
- `trusted` 放开全盘读取，但仍限制写入范围，避免“信任模式”变成“全盘可写”。

专用工具访问可读写根之外的路径时，会向用户发起审批：

- `o`：仅本次放行；
- `a`：永久批准目标所在目录；
- `d`：拒绝。

一次性批准只在当前工具调用内有效，写操作的临时文件与备份也不会借机逃逸；永久批准会立即反映到后续工具调用和 Bash 的边界中。批准目录也可以通过 `/allow` 手动管理。为保持边界清晰，永久批准只保存在当前会话内存中，不跨进程持久化。

#### 第二层：Bash 的内核围栏

Bash 不进入逐次审批，因为每次执行都弹窗会失去交互价值；但它始终运行在内核围栏内：

- Linux 下优先使用 **Landlock**，同时限制读和写；
- Landlock 不可用时降级到 **bwrap**，仍然提供写围栏；
- 两者都不可用时直接拒绝执行，绝不退回无围栏运行；
- 系统读根按“命令能启动”的最小集合收敛，不再整目录放开 `/etc`、`/proc`、`/sys`；
- 写范围限定在项目目录、用户批准目录和必要的临时目录。

内核规则在 `exec` 之后继续生效，因此 `env`、`sh -c`、`xargs`、管道和重定向都无法绕过。应用层负责策略与审批体验，内核层负责最终边界，两者职责不重叠。

### 3. 上下文管理体系

编码任务往往持续几十轮，上下文管理直接决定 Agent 能否稳定工作。

- **历史只追加**：消息按顺序追加并分配稳定 id，不原地修改，便于回放、序列化和缓存；
- **Token 计量**：用模型返回的真实输入 token 校准历史基线，未计量部分做增量估算；
- **压缩**：接近上下文窗口阈值时，把较早的完整轮次压缩成摘要，保留最近若干轮原文；
- **降级**：摘要模型不可用时退回简单摘要，不阻塞主流程；
- **输出截断**：工具输出按行数和字节数截断，完整内容保存到独立文件；
- **可观测性**：每轮模型输出、工具调用与结果写入 trace，便于复盘失败路径。

这套机制的目标不是“塞进更多内容”，而是在有限预算内保留最有用的信息。

### 4. 评测系统

只看模型输出很难判断一个编码 Agent 是否真的完成了任务，因此项目提供两条评测路径。

**本地 SWE 风格 harness**

对每个实例：

1. 建立干净的 git 仓库；
2. 运行 Agent，让它修改代码；
3. 提取 `git diff` 作为模型补丁；
4. 应用 golden 测试；
5. 运行测试并解析结果；
6. 按 `FAIL_TO_PASS` 与 `PASS_TO_PASS` 判定是否解决。

只有原有失败用例全部转绿、且原有通过用例没有回归，实例才算 `RESOLVED`。结果以 JSONL 落盘，同时保留补丁、测试结果与步数/token/耗时等指标。

**真实 SWE-bench 容器执行**

`eval/run_predictions.py` 支持在 SWE-bench 实例容器内运行 Agent，并把工具操作通过容器执行：

- 默认关闭容器网络，强制 pip / HuggingFace 离线；
- Agent 循环在宿主机，文件读写与命令执行进入实例容器；
- 产出官方格式的 `predictions.jsonl`，可交给 SWE-bench 官方 harness 判分；
- 同时输出每实例的诊断信息，便于定位“没改对”还是“没跑起来”。

---

## 快速开始

要求 Python 3.10+。安全围栏以 Linux 为目标平台；Bash 没有可用的沙箱后端时会 fail-closed，而不是无围栏执行。

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置模型
cp .env.example .env
# 编辑 .env，至少填入 LLM_API_KEY

# 3. 启动
python main.py
# 或
python -m agents
```

启动后进入多轮交互式会话，输入 `exit` 或 `quit` 退出，`Ctrl+C` 中断当前会话。

---

## 使用方式

```text
(build) 你> 帮我看看 agents/core/mode.py 里 plan 模式的沙箱策略
...
(build) 你> /mode plan
(plan)  你> 阅读 agents/fs/policy.py 并总结写授权逻辑
```

- 括号内为当前模式（`build` / `plan` / `trusted`）；
- 思考过程与正文均以流式输出；
- 每次工具调用会打印工具名和结果摘要。

### 常用命令

| 命令 | 说明 |
| --- | --- |
| `/mode plan` | 切换到只读的 plan 模式 |
| `/mode build` | 切换到可写的 build 模式 |
| `/mode trusted` | 切换到全盘可读、写仍受限的 trusted 模式 |
| `/allow` | 查看当前读/写批准目录 |
| `/allow read <path>` | 添加可读批准目录 |
| `/allow write <path>` | 添加可写批准目录 |
| `/allow remove read\|write <path>` | 移除批准目录 |
| `exit` / `quit` | 退出 |

命令在提交给模型之前处理，未识别为命令的输入才进入对话。

---

## 项目结构

```text
MyCode/
├── main.py                 # 交互式入口：流式输出、命令注册
├── agents/
│   ├── agent/              # CodeAgent 主循环与框架内置 Agent
│   ├── tools/              # 工具注册、专用工具、Bash、路径授权
│   ├── fs/                 # 路径身份与读写策略
│   ├── sandbox/            # Landlock / bwrap 内核围栏
│   ├── context/            # 历史、Token 计量、摘要与截断
│   ├── cli/                # /mode、/allow 命令体系
│   ├── core/               # LLM 客户端、流式协议、配置、生命周期
│   ├── observability/      # trace 记录
│   └── skill/              # 技能加载
├── eval/                   # 本地 harness 与 SWE-bench 容器执行
├── test/                   # pytest 单元与集成测试
└── requirements.txt
```

---

## 测试

```bash
python -m pytest test/
```

当前测试覆盖：

- 模式切换、策略判定与审批决策；
- 文件工具的读写鉴权与一次性/永久批准；
- Landlock 启动器、读写围栏、后端选择；
- Bash 在围栏内的执行与拒绝识别；
- CLI 命令、历史管理与补丁提取。

沙箱相关集成用例需要 Linux Landlock 支持。

---

## 环境变量

复制 `.env.example` 为 `.env` 后填写：

| 变量 | 必填 | 说明 |
| --- | --- | --- |
| `LLM_MODEL_ID` | 是 | 模型 ID，如 `deepseek-chat` |
| `LLM_API_KEY` | 是 | 模型 API Key |
| `LLM_BASE_URL` | 是 | 兼容 OpenAI 格式的服务地址 |
| `LLM_TIMEOUT` | 否 | 请求超时（秒），默认 60 |

兼容 DeepSeek、Qwen、Kimi、智谱、Ollama 等 OpenAI 格式接口。

---

## 框架与本项目的边界

本项目以 `40d5ba6 "引入框架"` 为分界：该提交带入的代码属于 HelloAgents 原生能力；此后新增的代码，以及对框架文件的修改，均为本项目自研。

### 框架原生

| 模块 | 内容 |
| --- | --- |
| `agents/agent/` | `SimpleAgent`、`ReActAgent`、`ReflectionAgent`、`PlanSolveAgent` 四种 Agent 实现 |
| `agents/context/` | `truncator.py` 工具输出截断的初始实现 |
| `agents/core/` | `exceptions.py`、`lifecycle.py`、`llm_error.py`、`session_store.py`、`streaming.py` 等基础协议与运行时 |
| `agents/observability/` | `trace_logger.py`，JSONL / HTML trace 记录 |
| `agents/skill/` | `loader.py`，技能加载与渐进式披露 |
| `agents/tools/` | `circuit_breaker.py`、`errors.py`、`response.py` 等工具基础设施 |
| `agents/tools/builtin/` | `calculator.py`、`devlog_tool.py`、`skill_tool.py`、`task_tool.py`、`todowrite_tool.py` 等通用工具 |
| 工程基础 | `.env.example`、原始依赖声明等 |

### 本项目完全自研

| 模块 | 内容 |
| --- | --- |
| `agents/agent/code_agent.py` | 编码智能体主循环：Function Calling、流式事件、工具执行与截断、步数与 Token 预算、上下文压缩、收尾兜底 |
| `agents/core/mode.py` | `AgentMode`（plan / build / trusted）与 `ModeGuard`：模式到策略的映射、批准目录、审批入口 |
| `agents/core/approval.py` | 审批请求与决策模型、控制台审批器、无交互默认拒绝 |
| `agents/fs/` | 路径身份解析、读写策略、可读/可写根、`read_all` |
| `agents/sandbox/` | Landlock / bwrap 后端、Landlock 启动器、后端选择与通用 `argv` 包装 |
| `agents/cli/` | `/mode`、`/allow` 命令体系与命令行解析 |
| `agents/tools/builtin/` 新增 | `bash_tool.py`、`grep_tool.py`、`glob_tool.py`、`fs_access.py` |
| `eval/` | SWE-bench 风格 harness、Docker 实例沙箱、容器版工具、预测生成、补丁提取与判定 |
| `test/` | 模式、策略、审批、工具、沙箱、CLI、上下文与评测相关测试 |
| 文档 | `README.md` |

### 在框架文件上的改造

| 文件 | 改动内容 |
| --- | --- |
| `agents/core/agent.py` | 接入 `ModeGuard`；`HistoryManager` 接入窗口/摘要配置；移除旧的 Token 计数与压缩逻辑 |
| `agents/core/config.py` | 增加摘要 LLM 的 API Key / Base URL 配置，智能摘要默认开启 |
| `agents/core/message.py` | 增加历史 id、`tool_call_id`、`tool_calls`、摘要 `replace` 区间，以及 LLM 字典互转 |
| `agents/context/history.py` | 重构为只追加历史 + Token 校准/增量估算 + 快照 + 摘要压缩 |
| `agents/context/token_counter.py` | 删除；Token 计量职责合并进 `HistoryManager` |
| `agents/core/llm.py` | 新增带工具调用的流式接口 |
| `agents/core/llm_adapters.py` | OpenAI 适配器支持流式工具调用、思考过程与用量统计 |
| `agents/core/llm_response.py` | 新增流式 chunk 类型、工具调用结构、`reasoning_content` |
| `agents/tools/base.py` | 新增 `access_kind` / `path_params` 能力元数据与 `brief()` 结果摘要 |
| `agents/tools/registry.py` | 工具名大小写不敏感索引、查找、注销与熔断器键归一 |
| `agents/tools/tool_filter.py` | 只读/全量/自定义过滤器改为大小写不敏感 |
| `agents/tools/builtin/__init__.py` | 导出 `BashTool`、`GrepTool`、`GlobTool` |
| `agents/tools/builtin/file_tools.py` | Read/Write/Edit/MultiEdit 接入路径身份、读写鉴权、审批与派生路径二次围栏 |
| `main.py` | 从 `SimpleAgent` 改为 `CodeAgent` 流式入口；注册命令、模式守卫与工具策略 |
| `requirements.txt` | 调整依赖下限，移除 `tiktoken`，补充 `PyYAML`，测试/评测依赖标注为可选 |
| `.gitignore` | 增加运行日志、评测产物、`.opencode/`、临时文件等忽略项 |

---

## 来源与许可

### 框架来源

本项目以开源项目 **HelloAgents** 为底座，框架与本项目的具体边界见上一节。

- HelloAgents：https://github.com/jjyaoao/HelloAgents
- 引入提交：`40d5ba6 "引入框架"`

### 许可证

本项目是 HelloAgents 的衍生作品，遵循 **CC BY-NC-SA 4.0**（署名—非商业性使用—相同方式共享 4.0 国际）许可证，完整条款见仓库根目录 [LICENSE](LICENSE)。

- 需注明原作者；
- 修改后的作品须使用相同许可证；
- 不得用于商业目的；商业使用请联系原作者获取授权。
