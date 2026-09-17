# 迁移审计报告：super-agent（Go → Python）

审计日期：2026-09-17
审计版本：`super-agent-py` @ `ffcad78`（工作区干净）
参照仓库：`../super-agent-go`（只读，未修改）
方法：以 `docs/` 为规格；Go 测试作为验证参照；仅在 Go 实现与 `docs/` 不冲突时才把它当作参照。
已逐个通读每个 Go 源文件与每个 Go 测试文件，并与对应的 Python 实现比对。下文所有结论均通过阅读代码确认；
其中大小上限、重定向、DNS、时间戳与 ID 相关的结论，另外通过只读方式实际执行验证。未修改任何实现代码。

---

## 1. MIGRATION_COMPLETE = **NO**

有两项**规格（docs）明确要求**的行为属于 DIVERGED/MISSING，另有若干文档与其规定的代码不一致。
按既定判定规则——*只有当不存在任何属于必做范围的 PARTIAL、MISSING、DIVERGED、UNVERIFIED 项时才为 YES*——结论为 NO。

阻塞项：

| # | 项目 | 分类 | 规格依据 |
|---|---|---|---|
| B1 | `browser_fetch` 未拒绝内网 **DNS** 解析结果（只检查了 IP 字面量和 `localhost`） | MISSING | `docs/tools.md:94-95` |
| B2 | 时间戳不是纳秒精度，会话/轮次 ID 也不是由 `time.time_ns()` 生成；Go 写出的 9 位小数时间戳在任何一次重写时被截断为 6 位 | DIVERGED | `docs/contributing.md:130-131` |

不阻塞但必须修的文档缺陷：`docs/session.md:247`、`docs/architecture.md:76-82`、`docs/config.md:7`、
`docs/runtime.md:47-58`、`docs/session.md:116`、`docs/tui.md` 的遗漏项、`tests/fixtures/README.md`（详见 §6）。

迁移的其余部分均为 COMPLETE：101 个非测试 Go 源文件全部有 Python 对应实现，260 个 Go 测试函数全部有
Python 对应测试，所有自动化门禁均通过。

---

## 2. 验证命令执行结果

环境：macOS（darwin 24.6.0）、Python 3.12，在 `super-agent-py/` 下执行。

| 门禁 | 命令 | 结果 |
|---|---|---|
| 测试 | `uv run pytest -q` | **419 passed, 2 skipped**，12.33s |
| Lint | `uv run ruff check .` | **All checks passed!** |
| 格式化 | `uv run ruff format --check .` | **204 files already formatted** |
| 类型（strict） | `uv run pyright` | **0 errors, 0 warnings, 0 informations** |
| 架构测试 | `uv run pytest tests/architecture -q` | **9 passed** |
| 构建测试 | `uv run pytest tests/build -q` | **1 passed**（6.13s） |
| 并发子集（`-X dev`） | `uv run python -X dev -m pytest tests/runtime tests/tools -q` | **303 passed, 2 skipped** |
| 覆盖率 | `uv run pytest --cov=super_agent` | 8820 条语句中 **81%**（与 `status.md` 一致；非门禁） |
| 验收 | `uv run python scripts/smoke.py` | **smoke passed**（flag/退出码、流式输出、resize、回合中取消、`/clear`、`/compact`、`/undo`、`/quit`） |

2 个 skip 是 `tests/tools/test_isolation.py:184` 与 `:195`，条件为 `skipif sys.platform != "linux"`
（"bubblewrap is Linux-only"）。其 Go 原测试同样受 Linux 限制（`isolation_test.go:153-171`），
因此这是对等行为而非退化；Linux CI 会真正执行它们。

数量统计：Go 侧 37 个测试文件、260 个 `func Test*`；Python 侧 39 个测试模块、353 个 `test_*` 函数。

`status.md`（auto-memory）已**过期**：它声称 424 passed / 2 skipped、207 个已格式化文件，以及由
`tests/architecture/test_migration_map.py` 校验的 260 行 `tests/MIGRATION_MAP.md`。
这些产物在 `ffcad78` 上都不存在（见 §6）。

---

## 3. Go 组件 → Python 组件迁移矩阵

已逐一核对全部 101 个 Go 非测试源文件是否有同名 Python 对应文件。只有两个没有，且均已说明清楚。

### 3.1 顶层映射

| Go | Python | 分类 |
|---|---|---|
| `main.go` | `super_agent/__main__.py` + `super_agent/cli.py` | COMPLETE（拆分） |
| `app/*.go`（10 个文件） | `super_agent/app/*.py` | COMPLETE |
| `app/instructions.go`、`app/instructions/instructions.go` | `super_agent/app/instructions/__init__.py`（并在 `app/__init__.py:46` 再导出） | COMPLETE（合并；这两个名字在 Python 中无法共存） |
| `llm/{claude,deepseek,openai,factory}.go` | `super_agent/llm/*.py` | COMPLETE |
| `project/project.go` | `super_agent/project/project.py` | COMPLETE |
| `runtime/api_{model,machine,execution,engine,session}.go` | `super_agent/runtime/api_*.py` | COMPLETE（别名注意事项见 §6） |
| `runtime/machine/*.go`（13） | `super_agent/runtime/machine/*.py` | COMPLETE |
| `runtime/engine/*.go`（5） | `super_agent/runtime/engine/*.py` | COMPLETE + 1 个额外模块（`policy_ports.py`） |
| `runtime/execution/*.go`（10） | `super_agent/runtime/execution/*.py` | COMPLETE |
| `runtime/permission/types.go` | `super_agent/runtime/permission/types.py` | COMPLETE |
| `runtime/protocol/types.go` | `super_agent/runtime/protocol/types.py` + `run_context.py` | COMPLETE + 1 个额外模块 |
| `runtime/session/*.go`（11） | `super_agent/runtime/session/*.py` | COMPLETE |
| `runtime/telemetry/telemetry.go` | `super_agent/runtime/telemetry/telemetry.py` | COMPLETE |
| `store/{store,repository}.go` | `super_agent/store/{store,repository}.py` | COMPLETE |
| `tools/**/*.go`（18） | `super_agent/tools/*.py` | COMPLETE |
| `tui/**/*.go`（15） | `super_agent/tui/**/*.py` | COMPLETE + `tui/runtime.py` |
| `workspace/{context,workspace}.go` | `super_agent/workspace/{context,workspace}.py` | COMPLETE |

### 3.2 Python 独有模块（Go 无对应）

| 模块 | 存在原因 | 分类 |
|---|---|---|
| `super_agent/errors.py` | 替代 Go 的 `errors`/`context`：`JoinedError`（对应 `errors.Join`）、`errors_is`/`errors_as`、`Cancelled`（对应 `context.Canceled`） | COMPLETE（`docs/contributing.md` 已认可） |
| `super_agent/jsonutil.py` | 用显式 dataclass 编解码替代 `encoding/json` + struct tag | COMPLETE |
| `super_agent/timeutil.py` | 替代 Go 的 `time` 格式化（RFC3339Nano） | PARTIAL —— `now_id`/`now_turn_id` 是**死代码**；见 B2 |
| `super_agent/tui/runtime.py` | 重新实现 Bubble Tea 的 MVU 循环、按键解码与渲染器（Go 侧由第三方库提供，`main.go:42`） | COMPLETE |
| `super_agent/runtime/protocol/run_context.py` | 替代标准库 `context.Context`/`WithValue` | COMPLETE，但未在文档中登记（见 §6） |
| `super_agent/runtime/engine/policy_ports.py` | 把 Go 中**未导出**的 `policySetter`/`policyStore`/`policySnapshot`（`engine.go:27-38`）从 `engine.py` 移出，以避免各 mixin 之间形成导入环 | COMPLETE，但未在文档中登记（见 §6） |

依赖规则检查：以上模块都不引入被禁止的依赖边。`run_context.py` 只导入 `super_agent.errors`；
`policy_ports.py` 导入 `runtime.execution`，而这条边 Go 本来就存在（`engine.go:6`）；
`tui/runtime.py` 不是 `super_agent.runtime`，因此不影响 R1。

### 3.3 分区域行为对等性小结

| 区域 | 分类 | 说明 |
|---|---|---|
| `runtime/machine`（状态、事件、15 事件注册表、转换表、不变量、错误、13 个变更、4 个动作） | COMPLETE | 已对照 `docs/machine.md:56-70` 逐行核对变更**顺序**与动作计划；由 `tests/architecture/test_spec.py` 强制 |
| `runtime/engine`（单循环、5 条退出路径、`RunID` 陈旧过滤、原子提交、观察者在锁外） | COMPLETE | 循环与 `docs/runtime.md:47-58` 逐行一致；锁策略为 asyncio 下的有意差异（§6） |
| `runtime/execution`（队列、resolver、policy、命令分析器、approval store、run controller、executor/runner） | COMPLETE | 策略优先级、分类阶梯、`ApprovalKey` 规范化哈希均已复现 |
| `runtime/session`（turn、history、persistence、notifications、repository、emitter、attachments、checkpoint、export） | COMPLETE，但有 1 处 DIVERGED | “尽力而为”的持久化现在会向上抛错（§6） |
| `llm/`（factory、claude、openai、deepseek） | COMPLETE | 上限、默认值、错误文案完全一致 |
| `tools/`（registry、files、commands、bash、env、output、proc、nofollow、sandbox、web、lsp、mcp） | PARTIAL | `web.py` 的 DNS 缺口（B1）、重定向上限、`search` 的正则引擎/分行（§6） |
| `store/`、`workspace/`、`project/` | COMPLETE | 磁盘格式对等性已用 Go 写出的 fixture 验证（§7） |
| `runtime/telemetry` | COMPLETE | 类型、关联 ID、`log_path` |
| `app/`（config、session、agents、subagents、workflows、extensions、mcp、instructions、tui_adapter） | COMPLETE | 有一处刻意修正：模型由**已解析**的凭证构造（`session.py:106-113`），符合 `docs/config.md:37-41`；Go 参照实现会把原始占位符当作 token 发出 |
| `cli` / flag / 退出码 | COMPLETE | 手写的 `flag` 复刻还原了 Go 的报错文案、`--`、`-yolo=false`、`-h` 语义 |
| `tui/`（app、update、view、actions、conversation、styles、approval、attachments、commands、composer、transcript） | COMPLETE | 34 条命令目录与 Go 逐字节一致；文档中的每个按键、布局规则、状态映射均已实现 |

---

## 4. 缺失 / 部分完成 / 偏离 / 未验证的功能

### 4.1 规格强制要求项（阻塞）

**B1 —— `browser_fetch` 未拒绝内网 DNS 解析结果。分类：MISSING。**
`docs/tools.md:94-95` 规定该抓取“rejects local and private DNS results”；工具表行
（`docs/tools.md:34`）也写“with redirect, size, timeout, and private-address protections”。
`validate_public_url`（`super_agent/tools/web.py:138-151`）只检查 scheme、`localhost`/`*.localhost`
以及 IP **字面量**，从不解析域名。代码自己就写明了这个缺口：

> `web.py:106-109` —— “That leaves a DNS-rebinding window: a name that validated can resolve to a private
> address before the connection is made. Resolving the host here instead would close that window but needs
> a custom dialer.”

Go 用自定义 `DialContext` 关闭了这个窗口：解析全部 A/AAAA 记录，只要有一条非公网就拒绝，并在结果为空时
以 `browser could not resolve host` 报错（`super-agent-go/tools/web.go:72-90`）。因此 Python 允许抓取
解析到 `127.0.0.1`/RFC1918 的主机名。`browser could not resolve host` 这个错误在 Python 侧也没有对应实现。
两个仓库均无测试覆盖此项。

**B2 —— 纳秒级时间戳与 ID。分类：DIVERGED。**
`docs/contributing.md:130-131`（规范条目，位于 “Disk-Format Stability”）要求：*“Timestamps are RFC 3339
with nanosecond precision. Session and turn identifiers come from `time.time_ns()` formatted by hand,
because `strftime("%f")` only reaches microseconds.”* —— 而同一节的 “field-level stability” 要求键名、
枚举字符串、**时间戳格式**、文件权限位与重放语义都不得改变。

- `super_agent/store/store.py:651-661`（`_idText`）把小数部分格式化为
  `f"{utc.microsecond:06d}000"` —— 9 位中的最后 3 位**恒为 0**。已实测确认：
  `new_id(datetime(2026,9,16,12,0,0,123456))` → `20260916T120000123456000`。
  `store/repository.py` 用的正是 `new_id`/`new_turn_id`（`store.py:227-232`）。
- `super_agent/timeutil.py:48-67`（`now_id`、`now_turn_id`）**确实**按要求使用了 `time.time_ns()`，
  但在 `super_agent/` 内**没有任何调用方** —— 合规实现是死代码。
- 对 Go 写出的数据，往返是有损的。已实测确认：
  `parse_rfc3339_nano("2026-09-16T12:00:00.123456789Z")` → `format_rfc3339_nano(...)` →
  `"2026-09-16T12:00:00.123456Z"`（`.123456789` → `.123456`）。每次重写 `meta.json` 都会触发
  （`store.py` 的元数据刷新、`set_current_turn`、`rename_session`、`save_workspace_description`），
  每次重写 `events.jsonl` 也会（`/undo` 过程中的 `truncate_after`）。
- 仓库内检入的 golden fixture 正好带有这个有损值：
  `tests/fixtures/session_store/20260916T120000123456789/meta.json` 中
  `created_at: 2026-09-16T12:00:00.123456789Z`、`current_turn_id: 20260916T120000.123456789`，
  这是 Python 的生成器无法产出的值，而 Python 一旦重写就会把它静默截断。

没有任何测试发现这一点：`test_session_store.py:713-714` 反而断言 ID 以 `000` 结尾；golden 重放测试
追加一条消息（会重写 `meta.json`）之后并未断言 `created_at`。

### 4.2 相对 Go 参照实现的偏离（文档未规定，故不阻塞，但确属实际差异）

| # | 结论 | 分类 | 证据 |
|---|---|---|---|
| D1 | “尽力而为”的持久化并不尽力而为：`store/repository.py` 在每次写失败时**先记日志再重新抛出**（`save_message` :61-66、`save_approval` :77-81、`save_error`、`save_cancel`、`save_reset`、`save_conversation_replacement`、`save_compaction`、`save_checkpoint`），而 `PersistenceMixin`（`runtime/session/persistence.py:27-44`）不做捕获。Go 会丢弃这些错误（`persistence.go:9-26`、`session.go:34-45,118-124`）。如今一次失败的 append 会经 `snapshot_emitter.emit` → `turn.py:174` 向上传播并中断 `run_turn`；`session.cancel` 可能抛错；`Session.close` 可能跳过全部已注册的 closer。这与该模块自己的 docstring 矛盾：“Each of these is best effort on purpose: a transcript that cannot be written must not fail the turn the user is watching”（`persistence.py:3-5`）。 | DIVERGED | `persistence.py:1-44`、`store/repository.py:61-149` |
| D2 | `search` 使用 Python 的 `re`（`files.py:196`），而 Go 用 RE2（`regexp`）。接受的语法不同（环视/反向引用），且模型给出的查询现在可能触发灾难性回溯（RE2 是线性时间）。 | DIVERGED | `files.py:196`、`files.go:132` |
| D3 | 对以 `\n` 结尾的文件，当模式能匹配空串时，`search` 会多出一行幽灵结果：`content.split(b"\n")`（`files.py:349`）会产出末尾的 `b""` 元素，而 Go 的 `bufio.Scanner` 不会。已实测确认：对 `b"alpha\nneedle\n"`，Python 扫描 3 行，Go 扫描 2 行。 | DIVERGED | `files.py:346-357` |
| D4 | `browser_fetch` 重定向上限：Python 最多跟随 5 次重定向 / 6 次请求（`_max_redirects = 5`、`for hop in range(6)`，`web.py:30,117-125`）；Go 最多 4 次（`len(via) >= 5`，`web.go:91-97`）。连接超时被并入 20 秒总超时（Go 另有独立的 10 秒拨号超时）。 | DIVERGED | `web.py:28-32,110-125`、`web.go:71,91` |
| D5 | `workspace.read_attachment` 的 MIME 识别是手写的（`workspace.py:257-289`），比 Go 的 `http.DetectContentType`（`workspace.go:189`）窄 —— 缺少 HTML/XML/audio/video/TIFF/MP4/ICO 等签名，例如 HTML 会被标成 `text/plain`。该值仅在内存中使用，不落盘。 | DIVERGED | `workspace.py:257-289` |
| D6 | `app.MCPController.add` 的 rollback 错误可能掩盖原始失败：rollback 调用没有在 `except` 内做保护，而 `remove` 做了（`app/mcp.py:78-88` vs Go `mcp.go:54-59`，后者会丢弃 rollback 错误）。 | DIVERGED | `app/mcp.py:78-88` |
| D7 | `jsonutil` 有意不复现 Go 的 HTML 转义（`\u003c`、`\u003e`、`\u0026`、U+2028/2029）。已记录为有意取舍；只有当产物含这些字符时才可观察（fixture 中不含）。 | DIVERGED（已接受） | `jsonutil.py:15-17` |
| D8 | 引擎锁策略：`_record_stream_chunk`（`engine/action_loop.py:377-396`）与 `query.py` 的全部读取方法（`query.py:36-87`）不加锁，而 Go 会取 `e.mu`。在 asyncio 下是安全的（临界区无 await、整体替换单一属性），并由 `test_concurrency_stress.py` 钉住；读者只会看到旧的或新的 `RuntimeData`，不会看到撕裂状态。 | DIVERGED（惯用法） | `action_loop.py:377-396`、`query.py:36-87` |
| D9 | 丢失的错误分支：`os.path.expanduser`/`os.getcwd()` 不会失败，而 Go 的 `UserHomeDir`/`Getwd` 会返回错误（`app/config.py:264,270,434-436`）；`ancestorDirs` 把不存在的 cwd 当作文件而不是报错（`app/instructions/__init__.py:79-80`）；`load plugin`/`load skill` 的错误上下文丢失，且 `discoverCommands` 不再跳过目录（`app/extensions.py:151,174,204-208`）。 | DIVERGED（次要） | 见左列 |
| D10 | `transition()` 没有 `None` 事件的保护，而 Go 会返回 `UnexpectedEventError`（`transition.py:202` vs `transition.go:70-72`）—— 在类型系统下不可达。 | DIVERGED（不可达） | `transition.py:202` |
| D11 | `runtime.api_execution` 缺少 Go 的 `DecisionNeedsApproval`/`DecisionRunDirectly`/`DecisionDenied` 别名与 `ApprovalWaitFunc`（`api_execution.go:11-15,50`）。三个决策值可通过 `ToolDecision.DECISION_*` 访问；`ApprovalWaitFunc` 是 Go 特有的 func→interface 适配器，Python 无此需要。 | MISSING（facade 别名，低） | `api_execution.py:11-55` |
| D12 | `app/instructions.py` 与包的合并是正确的，但 `decodeExtensions`（`app/config.py:375-388`）把 settings 文本又解析了第二遍，而共享编解码器已在 `config.py:455` 解出 `extensions` 块。属于冗余；未证明有行为差异。 | UNVERIFIED（冗余代码） | `config.py:375-388,456` |
| D13 | **未声明的直接依赖。** `super_agent/llm/openai.py:17` 导入 `httpx2`，但 `pyproject.toml` 未声明它（只声明了 `httpx>=0.28`；`tools/web.py:20` 用的是 `httpx`）。`httpx2` 目前经 `anthropic`/`openai` 传递解析（`uv.lock:21,222,506`，实际安装 2.13.0），所以今天能用，但本仓库自己的模块依赖他人包的传递依赖，一旦任一 SDK 移除它就会以 `ImportError` 崩溃。Go 两个场景共用一个客户端（`net/http`）。 | DIVERGED（打包） | `llm/openai.py:17`、`pyproject.toml:7-16` |

### 4.3 不适用项

本范围内没有真正平台特有的遗漏，除了一对对的构建标签分支，它们全部存在且分派正确：
`sandbox_linux`/`sandbox_other`、`proc_unix`/`proc_other`、`nofollow_unix`/`nofollow_other`。
Linux bubblewrap 行为是**已实现**的，并非被省略；只是在本 macOS 机器上无法执行（Linux CI 会执行）。

---

## 5. 缺失或薄弱的测试

未把覆盖率百分比本身当作证据；下表按 Go 测试函数逐个映射。

### 5.1 对等性

全部 **260** 个 Go 测试函数都有 Python 对应测试。Python 测试总体**更强**：353 个测试函数，其中包括 Go
完全没有的整块模块（`tests/runtime/test_command_analyzer.py`、`test_session_notifications.py`、
`test_concurrency_stress.py`），以及已映射模块内的额外用例（转换注册表的错误注册、重复键拒绝、
按字节偏移的损坏报错、检入的 golden store 重放、MCP 截止时间/环境变量/截断、LSP 在 cwd 变化时重连、
`strict_sandbox_fails_closed_off_linux`、依赖规则的正反样本对照）。

### 5.2 无 Python 对应实现的 Go 测试

| Go 测试 | 缺口 | 分类 |
|---|---|---|
| `TestOpenAIModelSkipsAuthHeaderWithoutAPIKey`（`tests/llm/openai_test.go:328`） | 断言在未配置密钥时**不发送** `Authorization` 头。Python 最接近的测试（`test_openai.py:377-404`）断言的是相邻性质（环境变量密钥*会被*使用）。`test_openai.py:382-387` 的 docstring 说明 SDK 拒绝在完全无凭证时构造客户端，因此“无密钥”这一分支无法被钉住。 | PARTIAL（有正当理由） |

其余 28 处函数名不完全对应的项均已逐一检查，属于换名但行为覆盖相同的重复项，例如
`TestReadFileTruncatesAtExactLimit` → `test_read_file_truncates_at_the_exact_limit`、
`TestBashToolsExposeRiskyBashTool` → `test_bash_tool_is_risky`、
`TestConcurrentRunTurnFailsWithoutBlockingEvents` → `test_session_run_turn_refuses_a_second_turn`。

### 5.3 薄弱测试

| # | 薄弱点 | 分类 |
|---|---|---|
| W1 | **tools 测试跑在手写替身上，而不是生产 workspace 策略上。** `tests/tools/test_workspace.py:36` 定义了 `FakeWorkspace`，其 `resolve_path`/`can_read`/`can_write` 重新实现了规范路径包含、各 root 的访问模式与“最近存在祖先”查找（含 `_canonical_nearest`，`test_workspace.py:119-133`）。其他每个 tools 测试都导入它（`test_bash.py:12`、`test_files.py:23`、`test_command_tools.py:23`、`test_lsp.py:22`、`test_web.py:19`、`test_isolation.py:26`、`test_workspace_acceptance.py:22`）。Go 的对应 helper 用的是**真正的** `workspace.NewDefaultContext`/`NewContext`（`tests/tools/workspace_test.go:10`、`files_test.go:138-140,174`）。后果：`super_agent/workspace/context.py` 出现回归无法让任何 tools 测试失败；生产策略只被 `tests/workspace/test_context.py`（4 个用例）直接覆盖。 | PARTIAL |
| W2 | 文档化的大小上限在**两个仓库里都没有测试**：`read_file`/`apply_patch` 的 10 MiB 拒绝、`search` 的 1 MiB 单行上限、`browser_fetch` 的 2 MiB 上限、重定向上限、内网 DNS 拦截。（128 KiB 的指令文件上限*有*测试 —— `test_instructions.py:123`。） | PARTIAL |
| W3 | 没有测试触发 repository 写失败，因此 D1 的“尽力而为”偏离不可见。 | PARTIAL |
| W4 | 没有测试在重写后重新读取 `meta.json`，因此 B2 的时间戳截断不可见。 | PARTIAL |
| W5 | `MCPController` 失败回滚与 `restart` 在两个仓库中都无测试（D6）。 | PARTIAL |
| W6 | 当工具链不存在时，`format`/`go_test`/git 相关测试会 skip（`test_command_tools.py:25-27`、`test_workspace_acceptance.py:24`），因此在没有 Go/git 的机器上不提供任何信号。 | NOT_APPLICABLE（环境） |
| W7 | 2 个 bubblewrap 沙箱用例在 macOS 上 skip（`test_isolation.py:184,195`）；Go 原测试同样受 Linux 限制。Linux CI 会执行。 | NOT_APPLICABLE（环境） |

没有任何 Python 测试被无条件 skip 或 xfail；所有标记都是平台/工具链守卫。

---

## 6. 架构差异

1. **依赖规则。** `tests/architecture/test_dependencies.py` 强制 R1–R8，是 Go `dependencies_test.go`
   的严格**超集**。Go 的每条规则（tui-vs-runtime、llm/tools-vs-runtime、store/workspace/project
   vs runtime、facade-vs-adapters、session-vs-store/tui/`os`/`filepath`、TUI 各 feature 互不导入）都在；
   R5 更强（对 `runtime/session/**` 递归，并额外禁止 `pathlib`），R7（machine 纯净性）是 Python 独有新增。
   已核实：`runtime/session/*.py` **不含** `os` 或 `pathlib` 导入；`store/repository.py:14` 与
   `workspace/workspace.py:21` 按允许的方式导入 `runtime.session`；只有适配器/组合根（`app/*.py`）
   导入根 `runtime` facade。
2. **`runtime/engine/policy_ports.py` 未在文档中登记。** `docs/architecture.md:76-82` 把
   `runtime/engine` 精确枚举为 `engine.py`、`commands.py`、`action_loop.py`、`query.py`。该额外模块承载
   Go 未导出的 `policySetter`/`policyStore`/`policySnapshot`，未重复定义任何
   `permission`/`execution` 类型；但按本仓库自己的文档优先规则（“Documentation is the specification for
   the code”），这个文件必须被写进文档。分类：DIVERGED（文档）。
3. **`runtime/protocol/run_context.py` 未在 `docs/` 中登记**（只有 `AGENTS.md:38` 提到
   `RunContext`）。它是标准库 `context.Context` 的替代，端口签名需要它。分类：PARTIAL（文档）。
4. **工具钩子接线方式改变。** Go 通过 `tools.Registry.SetToolObserver` 安装生命周期钩子
   （`session.go:180-183`）；Python 改为包装 runner（`app/session.py:249-301` 的 `toolHooks`），
   而注册表的 observer 通路（`tools/registry.py:36,55,133-156`）在**生产代码中完全未被安装**
   （只有 `tests/tools/test_hooks.py` 用）。可观察行为得以保留（pre 钩子失败则中止；
   post 钩子失败仅在工具本身成功时追加到输出；钩子走 `run_direct` 因而不会递归）。
   分类：DIVERGED（内部接口缝）。
5. **`docs/session.md:247` 把初始 system 消息的位置写错了。** Code Map 写的是“Initial system
   message — `app/system_prompt.py`, `app/session.py`”，而它现在位于 `app/agents.py:264`
   （`initialMessages`）。分类：DIVERGED（文档）。
6. **规格文档集中的缺陷与残留：**
   - `docs/config.md:7` 写 `.env` 由 “via `godotenv`” 加载 —— 那是 Go 的库；本移植用的是
     `python-dotenv`（`__main__.py:44-45`）。
   - `docs/runtime.md:47-58` 在 ```python 代码块里写入了 Go/Java 风格的标识符
     （`self._action_queue.Pop()`、`self._runtime_data.State`、`StateIdle`、`self._runs.FinishRun`），
     这些在 Python 中都不存在（应为 `pop()`、`.state`、`STATE_IDLE`、`finish_run`）。
   - `docs/session.md:116` 写成 `store.messagesFromRecords`（Go 拼写），实际是
     `store.messages_from_records`。
   - `docs/tui.md` 未记录 `/reset` 与 `/exit`，但目录/处理器中都有（继承自 Go，属既有规格缺口）。
7. **M9 的“迁移映射表”产物已被有意删除，现在只有过期残留。**
   `git show --stat c38d9ef` 删除了 `scripts/migration-map.py`（239 行）、`tests/MIGRATION_MAP.md`
   （286 行）与 `tests/architecture/test_migration_map.py`（161 行）。遗留的
   `tests/architecture/__pycache__/test_migration_map.cpython-312-pytest-9.1.1.pyc` 是那次提交之前的
   过期字节码（`co_filename` 仍指向已删除的源文件）；`docs/`、`AGENTS.md`、`README.md`、`tests/`、
   `scripts/` 中都已不再引用迁移映射表，因此当前没有任何规则要求它存在 —— 但“每个 Go 测试都已映射”
   这一结论不再由机器校验。本报告（§5）改为人工核查。
8. **CI 缺陷（`status.md` 已记录为已知问题，仍然存在）。** `.github/workflows/ci.yml:17` 向
   `astral-sh/setup-uv@v6` 传了 `python-version-file`，而合法输入名是 `version-file`，因此该 pin 被静默
   忽略。由于 `requires-python = ">=3.12"` 仍会解析出 3.12，门禁不受影响。
9. **TUI 运行时。** `super_agent/tui/runtime.py` 是第三方 Bubble Tea 循环的 Python 对应实现
   （Go 见 `main.go:42`），接线在 `__main__.py:66-81`。它不是缺失的 Go 组件，且由 Python 独有的测试覆盖
   （`tests/tui/test_app.py:851,932`）。`tui` 不导入 `runtime`；`AgentStatus` 仍是纯展示类型，
   映射放在 `app/tui_adapter.py`。

---

## 7. 与 `~/.superagent/` 的持久化数据兼容性

### 7.1 兼容项（已逐字段核实）

| 方面 | 状态 |
|---|---|
| `~/.superagent/sessions/<session-id>/` 目录布局、`meta.json` + `events.jsonl` | COMPLETE |
| `meta.json` 键与顺序：`id, title, created_at, updated_at, provider, model, cwd, instruction_fingerprint, instruction_sources, current_turn_id, parent_id, project_id, config_root, workspace` | COMPLETE（Go struct tag 与 `json_field` 逐项比对） |
| `workspace` → `WorkspaceSpec{primary_root, cwd, roots}`、`WorkspaceRootSpec{path, access}`、访问模式字符串 `read`/`read_write` | COMPLETE |
| `events.jsonl` 的 10 种记录类型：`session_started, message_appended, approval_decision, tool_result, cancel, reset, error, checkpoint, compact, context_replaced`，载荷键与 `omitempty` 行为一致 | COMPLETE |
| 重放规则（5 种产生消息的类型 vs 仅审计类型）、权限位 0600/0700、零值时间字符串、紧凑/缩进分隔符、map 键排序 | COMPLETE |
| 创建顺序（先写 transcript，最后写 `meta.json`，失败时删除目录） | COMPLETE |
| reset 的重放规则（丢弃非 `system`，保留 `system`） | COMPLETE |
| undo（选最近的**非空** checkpoint；**先**恢复文件系统再截断 transcript；`truncate_after` 走临时文件 + fsync） | COMPLETE |
| 压缩（默认保留 4 条消息、工具窗口扩展、同时持久化 `original_messages` 与 `kept_messages`） | COMPLETE |
| 一次性的旧版 `cwd` → `WorkspaceSpec` 升级（`save_workspace_description`）、此后走严格路径、幂等 | COMPLETE |
| `~/.superagent/settings.json` 的键与首次运行模板（内容等价；Python 的写入**更**原子 —— mkstemp/fsync/replace，`config.py:459-481`） | COMPLETE |
| 其他路径：`~/.superagent/AGENTS.md`、`~/.superagent/{commands,skills,plugins}`、`<cwd>/.superagent/*`、`<workspace>/.super-agent/{worktrees,exports}`、`~/.superagent/telemetry.jsonl` 默认值 | COMPLETE |

**Golden fixture 的来源（决定性的互操作证据）。** `tests/fixtures/session_store/` 下的 5 个内容文件是从
移植提交时的 `tests/fixtures/go_sessions/` **逐字节**移动过来的（`similarity index 100%`），而当时的
README 明确说明它们由 **Go** 实现写出。它们不可能是 Python 产出的：`.../20260916T120000123456789/meta.json`
带有 `created_at: 2026-09-16T12:00:00.123456789Z` 与 `current_turn_id: 20260916T120000.123456789`，
而 Python 的生成器在小数部分末尾永远是 `000`（§B2）。
`test_store_replays_a_checked_in_session_store`（`test_session_store.py:756-859`）能成功重放它们，
说明 Python 可以正确读取 Go 写出的 store。*文档退化：* `tests/fixtures/README.md` 被改写后不再说明其
Go 来源，却保留了如今无从解释的一句 “nothing here is hand-written” —— 该 fixture 之所以有价值
（即与“Python 自己没产出过的产物”做互操作验证）已不再被记录。

### 7.2 兼容性问题

| # | 问题 | 严重度 |
|---|---|---|
| P1 | **Go 写出的亚微秒时间戳在任何一次 Python 重写时被截断**（`.123456789Z` → `.123456Z`），包括每次 `meta.json` 重写与每次 `/undo` 对 `events.jsonl` 的截断。已实测证明；无测试覆盖；golden fixture 中正好含此类值。违反 `docs/contributing.md:126-131`。 | DIVERGED（必做） |
| P2 | **Python 永不输出亚微秒级 ID**（`...123456000`），因此其标识符精度低于 Go，且不像 `docs/contributing.md:130-131` 要求的那样来自 `time.time_ns()`。格式仍是 9 位、双方仍可互相解析，所以 Go 能读取 Python 的 store。 | DIVERGED（必做） |
| P3 | 未复现 HTML/`U+2028` 转义，因此含 `<`、`>`、`&` 的记录与 Go 输出存在**逐字节**差异（值在语义上仍等价且可解析）。已记录为有意取舍；fixture 中不含这些字符。 | DIVERGED（已接受） |

文件系统兼容性的方向：Python **能正确读取 Go** 的 store（golden fixture 重放通过）。
**Go 也能读取 Python** 的 store —— 布局、键、记录类型、权限位与 9 位 ID 格式都未改变；
损失的只是精度，绝不是结构。

---

## 8. 确切的剩余工作

按优先级排序。以下工作在本次审计中均未实施（未修改任何实现代码）。

**达到 `MIGRATION_COMPLETE = YES` 所必需**

1. **`browser_fetch`：拒绝内网 DNS 解析结果**（B1）。连接前解析主机名，只要解析出的任一地址非公网就拒绝，
   并为解析为空的情形补上 Go 的 `browser could not resolve host` 错误。涉及
   `super_agent/tools/web.py:99-151`。补一个测试：解析到内网地址的主机名必须被拒（可用桩解析器或注入 transport）。
2. **纳秒级时间戳与 ID**（B2）。二选一：
   (a) 让 `store.new_id`/`new_turn_id` —— 以及 `save_checkpoint` 的 checkpoint id —— 改用
   `timeutil.now_id`/`now_turn_id`（当前是死代码），**并且**在读-改-写路径上不再让 Go 写出的时间戳经过
   `datetime`，使 `meta.json`/`events.jsonl` 能无损往返；
   (b) 或者有意识地修改 `docs/contributing.md:126-131` 与 `docs/session.md`，写明微秒级上限并删除
   `time.time_ns()` 的要求，然后删除已成死代码的 `timeutil.now_id`/`now_turn_id`。
   补一个测试：读入 9 位时间戳、重写文件、断言值未变（可扩展 `test_session_store.py:731-750`/`:756-859`）。
3. **修正文档**，使 `docs/` 与代码一致：
   - `docs/architecture.md:76-82`：把 `policy_ports.py` 加入 `runtime/engine` 的文件清单。
   - `docs/session.md:247`：把 “Initial system message” 改到 `app/agents.py`（并提及 `app/system_prompt.py`）。
   - `docs/contributing.md` 的 “Project Layout at a Glance”：补上 `runtime/protocol/run_context.py`。
   - `docs/session.md:116`：改为 `store.messages_from_records`（Python 拼写）。
   - `docs/config.md:7`：改为 `python-dotenv`，不是 `godotenv`。
   - `docs/runtime.md:47-58`：改用真实标识符（`pop()`、`.state`、`STATE_IDLE`、`finish_run`）。
   - `docs/tui.md`：补记 `/reset` 与 `/exit`。
   - `tests/fixtures/README.md`：恢复 Go 来源说明（该 fixture 是作为互操作对照保留的 Go 输出）。

**建议处理（相对参照实现的偏离与测试缺口；非规格强制）**

4. 明确并记录**“尽力而为”持久化的契约**（D1）：要么像 Go 那样在 `PersistenceMixin` 里吞掉 repository 失败
   （`persistence.py:27-44`），并补一个“repository 失败不会让回合失败”的测试；要么修改 docstring，
   并补一个“写失败会中断回合”的测试。同时给 `Session.close` 加保护，使 `cancel` 抛错时不会跳过 closer。
5. `search`（D2/D3）：先决定 RE2 语义与 Go 的分行方式是否属于契约。若是，则拒绝 RE2 不接受的模式，
   并消除幽灵尾行（按 `\n` 切分后丢弃末尾空元素，或改用 `splitlines()`）；若否，则在 `docs/tools.md` 记录该偏离。
6. `browser_fetch` 重定向上限（D4）：与 Go 对齐（最多 4 次），或把 5 次记录为有意行为。
7. 恢复 `TestOpenAIModelSkipsAuthHeaderWithoutAPIKey` 的覆盖，或在 `docs/` 中记录 SDK 使“无凭证”情形无法表达（W5.2）。
8. 让 tools 测试像 Go 一样指向**真正的** `super_agent.workspace.Context`，而不是 `FakeWorkspace`（W1）——
   或补上显式的集成用例，把真实 context 传入 `default_registry`/`sandboxed_registry`。
9. 为目前两个仓库都无测试的文档化上限补测试：`read_file`/`apply_patch` 的 10 MiB 拒绝、
   `search` 的 1 MiB 单行上限、`browser_fetch` 的 2 MiB 上限、重定向上限（W2）。
10. 修复 `.github/workflows/ci.yml:17`（`python-version-file` → `version-file`）（§6.8）。
11. 删除过期的 `tests/architecture/__pycache__/test_migration_map.cpython-312-pytest-9.1.1.pyc`，
    并二选一：恢复 M9 迁移映射表（文档 + 生成脚本 + 校验测试），或在 `status.md` 中记录 M9 产物已被有意删除
    （§6.7）。同时刷新 `status.md` —— 它当前报告的 424 个测试、207 个格式化文件与 `MIGRATION_MAP.md`
    在 `ffcad78` 上都不存在。
12. 次要项：给 `MCPController.add` 的回滚加保护（D6）、把 `read_attachment` 的 MIME 识别扩展到 Go 的签名集合
    （D5）、补回丢失的错误分支（D9）、在 `pyproject.toml` 中声明 `httpx2`（D13）。

---

## 附录：审计范围与限制

- 已完整通读：全部 101 个 Go 非测试源文件及其 Python 对应实现；全部 37 个 Go 测试文件（260 个 `func Test*`）；
  全部 39 个 Python 测试模块（353 个 `test_*`）；两个仓库的 `docs/*.md` 以及二者之间的 diff。
- 已执行：`pytest`、`ruff check`、`ruff format --check`、`pyright`、`pytest tests/architecture`、
  `pytest tests/build`、`python -X dev -m pytest tests/runtime tests/tools`、覆盖率、`scripts/smoke.py`，
  以及针对时间戳往返、ID 生成、`search` 分行行为的只读 Python 检查。
- 未执行：2 个 Linux 专属的 bubblewrap 沙箱用例（本 macOS 机器无法运行；Linux CI 会执行）、
  `scripts/build-local.sh`（会安装二进制，超出范围），以及 `docs/contributing.md` 中记录的、有意保留为人工的
  验收项（配色/布局可读性、终端相关按键、剪贴板）。
- `super-agent-go` 未被修改。
