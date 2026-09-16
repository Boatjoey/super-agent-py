# Workspace 工作区

本文件分两部分：

- **第一部分：Coding Agent Workspace 概念调研** —— 跨产品的行业调研，属于背景资料，不是本项目的规范。
- **第二部分：super-agent 的 Workspace 实现** —— 本仓库的实际实现说明。规范性行为以各自的 owner 文档为准（文内链接给出）；如本部分与 owner 冲突，以 owner 为准。

---

## 第一部分：Coding Agent Workspace 概念调研

### 定义、边界、主流产品共性与工程抽象

日期：2026-09-14

#### 结论先行

Workspace 在 Coding Agent 领域没有统一标准定义。VS Code、Claude Code、Gemini CLI、Codex 都使用了相近但不完全一致的概念。

从工程上抽象，它最稳定的共同含义是：

> Workspace 是一次 Agent 工作时的本地文件操作范围与运行目录描述。它定义 Agent 当前从哪里开始工作、允许访问哪些目录、各目录具有什么访问权限，并可作为 Session 恢复时重建运行环境的依据。

因此，Workspace 的核心不是“项目名称”，也不是“代码索引”，而是三个问题：

- 路径基准：相对路径相对于哪里解析，命令默认在哪里执行；
- 文件范围：Agent 可以访问哪些目录；
- 权限与生命周期：这些目录是只读还是可写，以及 Session 恢复时如何恢复同样的文件环境。

推荐把下列概念明确拆开：

| 概念 | 回答的问题 | 是否等同于 Workspace |
|---|---|---|
| Project | “这是哪个逻辑项目？” | 否 |
| Checkout / Worktree | “当前实际修改哪一份代码副本？” | 否 |
| Workspace | “这次 Agent 可以在哪些目录工作？” | 是核心对象 |
| Session | “这次对话/任务的状态是什么？” | 否，但应绑定 Workspace |
| Sandbox | “操作系统是否真正阻止越界访问？” | 否，是更底层的安全机制 |
| Code Index | “如何快速找到相关代码？” | 否，是检索机制 |

### 1. Workspace 为什么会出现

如果 Agent 只接收自然语言，而没有 Workspace，它无法稳定回答以下基础问题：

- `src/main.py` 应该相对于哪个目录解析？
- `python -m pytest` 应该在哪个目录运行？
- 搜索 `UserService` 应该搜索整个磁盘还是当前仓库？
- Agent 是否可以读取相邻仓库？
- Agent 是否可以修改一个额外依赖目录？
- Session 恢复后，是继续原来的目录，还是使用当前进程启动目录？

传统 IDE 早已有 Workspace 概念。VS Code 官方定义中，Workspace 是一个窗口中打开的一个或多个文件夹，并以 Workspace 为范围保存设置、任务、调试配置和 UI 状态。Multi-root Workspace 进一步允许一个 Workspace 同时包含多个独立目录。[1]

Coding Agent 在此基础上增加了两个要求：

1. Agent 会主动执行文件操作和命令，因此 Workspace 需要成为访问范围的一部分。
2. Agent Session 可以被恢复、并行或迁移，因此 Workspace 需要具备生命周期语义。

因此，Coding Agent 的 Workspace 比 IDE 的“打开哪些文件夹”更接近一个运行时环境描述。

### 2. 一个最小 Workspace 应包含什么

从第一性原理看，最小 Workspace 可以表示为：

```text
Workspace
  primaryRoot   主工作目录
  cwd           当前命令和相对路径的工作目录
  roots[]       允许访问的目录集合
    path
    access      read / read_write
```

例如：

```text
primaryRoot = /code/frontend
cwd         = /code/frontend

roots:
  /code/frontend   read_write
  /code/backend    read
  /code/shared     read_write
```

这表示 Agent 当前主要在 frontend 工作，但允许读取 backend，并允许修改 shared。

对应的运行时接口通常只需要回答几个问题：

```text
ResolvePath(path)   相对路径最终指向哪里
CanRead(path)       是否允许读取
CanWrite(path)      是否允许修改
GetRoots()          当前允许访问哪些目录
GetCWD()            命令默认在哪里执行
```

Workspace 的价值在于让所有文件工具、搜索工具、LSP、Git 和命令执行共享同一个目录语义，而不是每个模块自己调用进程 cwd 或自己推断项目目录。

### 3. Workspace 与 Project 的区别

这两个概念在最简单场景中经常指向同一个目录，因此容易混淆。

例如：

```text
Project:   my-app
Workspace: /code/my-app
```

但两者回答的问题不同：

- Project 表示逻辑身份：这是哪个项目，项目配置、历史、instructions 应归属于谁。
- Workspace 表示当前工作范围：这次 Agent 可以访问哪些目录，默认 cwd 在哪里。

当一个项目出现多个代码副本时，区别就很明显：

```text
Project: my-app

Local checkout:  /code/my-app
Worktree A:       ~/.agent/worktrees/task-a
Worktree B:       ~/.agent/worktrees/task-b
```

三个目录可以属于同一个 Project，但不同任务可以运行在不同的 Workspace/Checkout 上。

因此不建议长期使用 `projectId = workspacePath` 作为稳定模型。Project 是逻辑身份，路径只是某次代码副本的位置。

### 4. Workspace 与 Checkout / Worktree 的区别

Checkout / Worktree 解决的是“代码副本隔离”，Workspace 解决的是“本次 Agent 的文件访问范围”。

Codex 的公开文档明确区分 Local checkout 和 Worktree：Local 是用户已有的仓库目录；Worktree 是从该本地 checkout 创建的 Git worktree。Codex 可以为不同 chat 创建独立 worktree，并让 chat 在 Local 和 Worktree 之间 handoff；一个 chat 会保持与其 worktree 的关联。[6]

这说明：

```text
Project
  ├── Local checkout
  ├── Worktree A
  └── Worktree B
```

而一次 Session 的 Workspace 可以把某个 checkout 作为 primaryRoot，同时再允许访问额外目录。

所以：

- Checkout / Worktree = 实际代码副本
- Workspace            = 当前会话允许操作的文件范围

二者通常相关，但不能合并成一个概念。

### 5. Workspace 与 Sandbox 的区别

Workspace 通常首先是应用层策略。例如文件工具在真正读取前检查：

```text
path -> canonicalize -> containment check -> CanRead/CanWrite -> I/O
```

这可以约束 Agent 内建的 `read_file`、`write_file`、`search` 等工具。

但如果 Agent 可以执行 shell：

```text
cat ~/.ssh/id_rsa
```

shell 子进程直接使用操作系统文件系统 API，应用层的 `CanRead()` 并不会自动拦截它。

因此：

- Workspace policy 负责定义“应该允许什么”
- OS Sandbox 负责在进程级别真正强制“只能做什么”

Codex 的配置中，workspace-write sandbox 支持额外 `writable_roots`，说明 Workspace 范围会被下沉到 sandbox 权限中，但二者仍是不同层次。[7]

Gemini CLI 也体现了类似分层：配置中 `context.includeDirectories` 用于把额外目录加入 workspace context，而 sandbox 另有 `sandboxAllowedPaths`；其 macOS sandbox 实现会读取 `WorkspaceContext` 中的目录，再把它们映射到 sandbox 参数。[4][5]

因此，一个完整 Coding Agent 通常同时需要：

```text
WorkspaceContext    应用层路径策略
       +
OS Sandbox          子进程级强制隔离
```

### 6. Workspace 与 Config Root 的区别

“允许读取一个目录”不应自动等于“信任这个目录中的 Agent 配置”。

Claude Code 对这一点定义得非常明确：`--add-dir` / `/add-dir` 会增加 Claude 可以读取和编辑的工作目录，但不会让额外目录自动成为完整的 `.claude/` 配置根。大多数 hooks、subagents、commands 和 settings 仍只从主工作目录及其配置层级发现。[2]

Gemini CLI 也把两者分开：`context.includeDirectories` 用于扩展 Workspace；而 `context.loadMemoryFromIncludeDirectories` 默认是 `false`，只有显式开启后才从这些附加目录加载 `GEMINI.md` memory。[4]

因此推荐保持：

```text
accessRoots != configRoots
```

原因是两类权限含义不同：

- accessRoots：Agent 能否读写代码；
- configRoots：某个目录能否改变 Agent 的 instructions、hooks、skills 或其他行为配置。

这是 Workspace 设计中非常重要的安全边界。

### 7. Workspace 与 Session 的区别

Workspace 描述的是文件环境；Session 描述的是一次 Agent 对话或任务的状态。

它们不是同一个对象，但 Session 应该绑定 Workspace。

推荐区分：

- WorkspaceSpec：可持久化的数据描述
- WorkspaceContext：根据当前文件系统重新验证后得到的运行时对象

Session 可以保存 WorkspaceSpec：

```text
Session
  id
  projectId
  workspaceSpec
  conversation state
```

恢复时：

```text
load Session
  -> load WorkspaceSpec
  -> canonicalize / validate filesystem
  -> construct WorkspaceContext
  -> resume Agent
```

不应简单使用恢复时的进程 cwd 覆盖旧 Session 的 Workspace，否则同一个 Session 可能在不同目录中恢复，导致工具作用范围发生变化。

### 8. Workspace 与代码索引的区别

Workspace 回答：“Agent 可以在哪些文件中工作？”

代码索引回答：“在允许访问的文件中，怎样更快找到相关代码？”

两者应解耦。

Workspace 可以支持：

- ripgrep
- 文件树
- Tree-sitter symbol index
- semantic embedding index

其中任何一种检索实现都不应成为 Workspace 本身的定义。

否则会出现错误耦合，例如“创建 Workspace 就必须建立向量数据库”。实际上，小型或中型仓库完全可以只使用文件树、grep 和按需读取；大型仓库再增加结构索引或语义索引。

### 9. 主流产品中的共同模式

#### 9.1 VS Code：Workspace 是一个或多个打开的目录 + Workspace 级状态

VS Code 是理解该概念的基础参照。官方定义为：Workspace 是当前 VS Code 窗口中打开的一个或多个文件夹，并用于限定 Workspace 设置、任务、调试配置和 UI 状态。Multi-root Workspace 可以包含多个根目录。[1]

其核心是：Workspace 是一次开发环境的范围，而不是单个文件。

#### 9.2 Claude Code：主工作目录 + additional working directories

Claude Code 默认可以访问启动目录，并允许通过 `--add-dir`、`/add-dir` 或持久配置增加额外工作目录。[2]

同时，Claude Code 明确把文件访问与配置发现分开；Bash 的 cwd 也只能在项目目录和已添加工作目录之间保持，若 cd 到范围外会被重置到项目目录。[2][3]

体现出的 Workspace 模型是：

```text
主工作目录
+ additional directories
+ cwd 约束
+ 独立的 config discovery 边界
```

#### 9.3 Gemini CLI：显式 WorkspaceContext

Gemini CLI 的源码中直接存在 `WorkspaceContext`。其职责说明就是“管理多个 workspace directories 并对路径进行验证，使 CLI 可以在一次 session 中操作多个目录的文件”。它维护 target directory、additional directories 和 read-only paths，并通过真实路径解析和相对路径判断进行 containment 检查。[5]

配置层还提供 `context.includeDirectories`，支持把额外目录加入 Workspace；而 memory 是否从这些目录加载是独立开关。[4]

这是一种非常清晰的运行时实现：

```text
WorkspaceContext
  -> directories
  -> read-only paths
  -> path canonicalization
  -> containment / readability checks
```

#### 9.4 Codex：Workspace 权限与实际 checkout 分离

Codex 一方面提供 workspace-write sandbox 和额外 writable roots；另一方面在桌面工作流中把 Local checkout 和 Worktree 明确区分，并允许一个 chat 长期关联同一个 worktree。[6][7]

这说明成熟 Agent 通常不会把“项目身份”“代码副本”“Workspace 权限”“Session”压缩成单一 path。

### 10. 从这些产品抽象出的共同结构

可以把主流 Coding Agent 的共同结构归纳为：

```text
Project
  逻辑项目身份、配置归属
        |
        v
Checkout / Worktree
  当前实际代码副本
        |
        v
WorkspaceSpec
  primaryRoot / cwd / allowed roots / access mode
        |
        v
WorkspaceContext
  运行时路径解析与授权
        |
        +-------------------+
        |                   |
        v                   v
Filesystem tools        OS Sandbox
read/write/search       shell/process isolation
        |
        v
Code retrieval
ripgrep / symbols / semantic index

Session
  持久记录与恢复 Project + Workspace/Checkout 关联
```

这里最重要的是每层只解决一个问题。

### 11. 推荐的工程定义

如果需要在 Agent 项目中给 Workspace 一个明确、可落地的定义，可以采用：

> Workspace 是某个 Agent Session 的文件系统工作范围。它由一个主工作目录、当前 cwd、一个或多个允许访问的目录以及每个目录的访问模式组成；所有内建文件与搜索工具都应以它作为路径解析和访问授权的唯一运行时来源。Workspace 可以被 Session 持久化并在恢复时重新验证，但它不负责定义 Project 身份、配置可信范围、Git checkout 身份、OS 级 sandbox 或代码索引。

对应最小数据模型：

```python
@dataclass(frozen=True)
class WorkspaceSpec:
    PrimaryRoot: str
    CWD: str
    Roots: tuple[WorkspaceRootSpec, ...]


@dataclass(frozen=True)
class WorkspaceRootSpec:
    Path: str
    Access: AccessMode  # read | read_write
```

运行时对象：

```text
WorkspaceContext
  ResolvePath(path)
  CanRead(path)
  CanWrite(path)
  GetRoots()
  GetCWD()
```

### 12. 设计原则

综合上述产品和实现，Workspace 设计建议遵循以下原则：

1. **一个 source of truth**：文件、搜索、Git、LSP、格式化和命令 cwd 不应各自推断目录。
2. **支持 multi-root**：主目录之外可以有 additional roots，且每个 root 可以有不同访问模式。
3. **路径必须 canonicalize**：需要处理 `..`、绝对路径、symlink、路径前缀碰撞和不存在的新文件路径。
4. **Access 与 Config 分离**：获得文件访问权限不自动获得配置可信权限。
5. **Workspace 与 Sandbox 分层**：Workspace 是应用层授权；shell/MCP 等外部进程需要 OS 级隔离。
6. **Workspace 与 Project 分离**：未来支持 Git worktree、多 checkout 和项目移动时不应依赖单一路径作为 Project 身份。
7. **Workspace 与 Index 分离**：Workspace 定义搜索范围，Index 只是搜索实现。
8. **Session 持久化 Workspace 描述**：恢复旧 Session 时应恢复原文件范围，并重新验证当前文件系统状态。

### 13. 最终结论

Workspace 可以理解为 Coding Agent 的本地文件运行范围。

它的最低职责是：

```text
在哪里工作（cwd）
+
能访问哪里（roots）
+
能做什么（read / read_write）
+
Session 恢复时如何恢复相同范围
```

Project、Worktree、Sandbox、Config、Code Index 都围绕 Workspace 工作，但分别解决不同问题。工程上把这些概念拆开，才能支持 multi-root、session resume、subagent、worktree 和强 sandbox，而不会让一个 `cwd`/`path` 字段承担所有职责。

---

## 第二部分：super-agent 的 Workspace 实现

super-agent 已经实现了上述模型中的核心对象。它把三件容易被混为一谈的事情分开：**用户选中的是哪个项目**、**Agent 可以读写什么**、**为了一次后续 resume 需要保存什么策略**。

本部分是一份实现地图，不是第二份规范。每条规则只有一个权威出处，文中以链接给出；若本部分与 owner 文档冲突，以 owner 为准。

### 两个概念，而非一个

| 概念 | 类型 | 回答的问题 | 权威出处 |
|---|---|---|---|
| Project 项目 | `project.Project` | “这是哪个代码项目？” | `config.md`（flags）、`session.md`（initial context） |
| Workspace 工作区 | `workspace.Context` | “Agent 可以读写哪些 root，相对路径在哪里解析？” | `session.md`、`tools.md` |

二者刻意保持独立。增加一个可读或可写的 workspace root 只授予文件访问权限；它**不会**让该 root 下的 `AGENTS.md`、`CLAUDE.md`、hooks、skills、plugins 或配置被加载。指令与配置发现使用被选中的 project/config root，而不是访问 root —— 见 `session.md`。

### 四个类型

| 类型 | 文件 | 角色 |
|---|---|---|
| `project.Project` | `project/project.py` | 身份：解析出的 root 及其稳定 id。由 `project.Resolve` 产生。 |
| `workspace.Context` | `workspace/context.py` | 访问策略对象，也是**进程无关的运行时唯一真相来源**。持有一个 primary root、一个 cwd，以及若干带 `read` / `read_write` 标记的 root。 |
| `workspace.Workspace` | `workspace/workspace.py` | 一个由互斥锁保护的、**可切换**的 `Context` 绑定，同时是 session 文件系统适配器（checkpoint、attachment、export）。通过 `Spec`/`Validate`/`Canonicalize`/`Activate` 实现 `runtime/session.Workspace` 端口。 |
| `session.WorkspaceSpec` | `runtime/session/repository.py` | 可持久化的 JSON 描述：primary root、cwd、带访问模式的 roots。它**不携带授权语义** —— 校验与访问决策属于具体的 `workspace` 实现。 |

`store` 会把 `WorkspaceSpec` 与两个相互独立的字段 `ProjectID`、`ConfigRoot` 一起写入 session metadata；replay 期间三者互不派生（`session.md`）。

### 访问策略

路径策略集中在 `workspace.Context`：`ResolvePath`、`CanRead`、`CanWrite`，底层是规范路径（canonical）包含判断。内建的文件、搜索、命令、bash、git、format、LSP、checkpoint，以及 attachment/export 路径，全部经由注入的 context，而不是进程工作目录。具体的包含规则与 cwd 规则由 `tools.md` 规定；配置与访问的分离由 `session.md` 规定。

内建工具依赖一个窄端口 `tools.WorkspaceContext`（`GetPrimaryRoot`、`GetCWD`、`ResolvePath`、`CanRead`、`CanWrite`），而不是具体 context。命令工具在**调用时**从该端口取 sandbox bind root，因此一次切换了 context 的 resume 会改变后续命令的运行位置。

### 生命周期

```text
启动
  LoadConfig            解析 Project（--cwd、向上找 .git、否则进程 cwd）
                        由 project root 构造初始 Context
  NewSession            包成 workspace.Workspace
                        把同一个绑定注入 tools、命令 sandbox、LSP、session
  CreatePersistentSession
                        把 workspace.Spec() 持久化进 session metadata

turn
  内建工具、命令、LSP 都经由注入的绑定解析路径与访问权限

resume
  读取保存的 WorkspaceSpec
    存在           针对当前文件系统校验，然后原子激活
    legacy（仅 cwd）一次性 canonicalize，持久化得到的 spec，此后一律严格
```

每一步的规范出处：

- 启动解析，以及“resume 绝不回退到进程 cwd 或当前 `--cwd`”的保证：`session.md`（“Initial Context”、“/resume”）。
- 一次性 legacy 升级及其失败语义：`session.md`（“/resume”）。
- 工具注入、可切换的运行时绑定，以及恢复 cwd 变化后 LSP 的惰性重连：`tools.md`（“Registry”、“Built-in Tools”）。
- `--cwd` 与解析出的 project root：`config.md`（“Flags and Environment”）。

子 `delegate` agent 会针对它自己的、以子 cwd 为根的默认 context 运行。承载该子 agent 的 worktree 创建在父 workspace 之内，因此其写检查使用父绑定；见 `session.md` 与 `app/subagents.py`。

### 不变量

- **Spec 是数据，不是信任。** `WorkspaceSpec` 只是描述；`workspace.Context` 是把它针对**当前**文件系统重新校验后重建的对象。持久化的 context 从不被当作可信。
- **新版严格，legacy 宽松一次。** 已保存的 `WorkspaceSpec` 一律严格校验，绝不回退到裸 cwd。只有写入于 `WorkspaceSpec` 之前的 metadata —— 一个从未承诺 canonical 的裸 cwd —— 会被升级，且仅升级一次。
- **配置不是访问。** 指令与配置 root 与 workspace 访问 root 保持分离（`session.md`）。
- **不是操作系统安全边界。** workspace context 无法阻止一个已启动的 shell 读取 `~/.ssh/id_rsa`；当启用时，独立的 OS sandbox 负责约束子进程访问（`tools.md`）。

### 模块地图

| 路径 | 角色 |
|---|---|
| `project/` | 项目 root 解析 |
| `workspace/context.py` | 访问策略：规范包含判断、访问模式、cwd 解析 |
| `workspace/workspace.py` | 可切换绑定与 session 文件系统适配器 |
| `tools/workspace.py` | 面向内建工具的窄 `WorkspaceContext` 端口与读写解析 |
| `runtime/session/repository.py` | `WorkspaceSpec` DTO 与 `Workspace` 端口 |
| `runtime/session/history.py` | Resume：校验、激活、一次性 legacy 升级 |
| `store/` | spec 的 JSON 持久化，含 `SaveWorkspaceDescription` |
| `app/config.py`、`app/session.py`、`app/subagents.py` | 组合根接线 |

完整的包与文件职责由 `architecture.md` 拥有。
