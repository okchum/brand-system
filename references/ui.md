## 12. 产品 UI 视觉语言与代表性组件

交付的是可运行的品牌组件样例与规范，不是完整生产组件库或业务系统。明确哪些只是外观参考，哪些具备经过测试的交互行为。

范围包含：Button/IconButton、Link、Input/Textarea、Select、Checkbox、Radio、Switch、Tabs、Badge、Avatar、Tooltip、Card、Dropdown、Dialog/Drawer、Toast/Alert、Navigation/Sidebar、Breadcrumb、Pagination、Table、Empty/Loading/Skeleton。

覆盖适用的 default、hover、pressed、focus-visible、selected、disabled、loading、invalid 状态。不同组件不强行拥有完全相同状态集合。

至少展示：

- 一页信息密集型后台/工作台示例。
- 一页表单或设置界面，含错误提示与加载反馈。
- 一页与真实产品有关的核心流程片段及移动布局。
- 一页登录/欢迎或官网转化片段，按产品范围选定。

使用真实可读的演示文本，避免 Lorem ipsum 掩盖换行问题；用长名称、空数据、异常值和多语言检查布局。

使用原生语义与成熟交互原语，不凭空编造 ARIA。Dialog、菜单、Tabs 等若声明可交互，须有键盘、焦点、关闭和状态处理；否则清楚标为静态视觉样例。

数据密集界面应有层级、分隔、对齐、可读数字、选中态和密度规则，不能仅制作一张空旷营销卡片代表全部产品设计。

### 12.1 UI 契约与工作单元

`config/ui.json` 是 UI 入口配置，字段固定为：

- `version`：非空契约版本字符串。
- `platforms`：非空数组，只能使用 `web`、`desktop`、`ios`、`android`；不得写孤立的 `mobile`。
- `stackProfile`：只能是 `html-css-js` 或 `react`。
- `tokenSource`：固定为 `tokens/src`；UI 配置不得复制 token 值。
- `deliveryStatus`：只能是 `preview-only` 或 `handoff-ready`。没有可验证工具链时不得声称 native runtime 已实现。

`src/ui/ir/manifest.json` 是页面和组件工作单元的唯一清单，必须包含 `manifestVersion`、`hash` 和 `units`。每个 unit 必须包含 `id`、`kind`、`files`、`platforms`；`kind` 只能使用 `page-map`、`layout`、`reuse-analysis`、`component`、`page`、`platform-adaptation`，而且六种都必须出现。manifest 只描述设计内容，不记录进度。

工作顺序固定为：页面地图 → 布局 → 复用分析 → 组件 → 页面 → 平台适配。复用分析是独立的 unit，组件 unit 依赖它，所以组件必须按复用结论来做，不能边做边临时决定。每个 unit 都必须启动独立 subagent review；审阅记录写入 `project/approvals.json` 的 `kind=unit-review`，不能用一次总评替代逐单元记录。

每种 unit 的产出（都写在该 unit 的 `output.html`，可在 Web preview 中打开）：

- `page-map`：全部页面及其用途、入口与跳转关系、每个页面服务的用户任务；标出哪些页面只在部分平台出现。
- `layout`：每个页面的布局骨架与槽位（导航、内容区、侧栏、操作区等），以及各平台下的布局差异（Web 断点、Desktop 窗口、iOS/Android 安全区与导航）。
- `reuse-analysis`：一张组件清单，逐项写出：
  - 组件名称；
  - 出现在哪些页面、哪个布局槽位；
  - 结论：合并成一个共享组件，还是保持各自独立，并给出理由（职责相同、只差内容 → 共享；交互或数据语义不同 → 独立）；
  - 共享组件需要的变体与状态；
  - 引用的 token 名。

  不得把只是外观相似、语义不同的元素合并成一个组件。
- `component`：按复用分析的结论实现共享组件与必要的独立组件，覆盖本文件列出的交互状态；颜色、字号、间距只引用 token。
- `page`：用已有组件拼出各页面，不新造复用分析里没有的组件；确需新增时先写明理由。
- `platform-adaptation`：Desktop、iOS、Android 相对 Web 的差异与平台语义说明，用 Web preview 呈现，写清转换为 native 代码时的对应关系。

依赖只认 manifest 每个 unit 的 `dependsOn`：初始化按上面的顺序写入，之后工作台生成与 checker 都读同一份。`files` 在初始化时就写定为 `src/ui/units/<unitId>/output.html`，之后不再改写：hash 覆盖 `files`，推进过程中再填写会让所有已绑定的 review 失效。

#### unit 推进规则

进度只记在 `project/status.json` 的 `units` 里，状态只能是 `not-started`、`in-progress`、`in-review`、`approved`、`changes-requested`。

依赖满足：依赖 unit 处于 `approved`，且它最新一条 unit-review 结论为 `approved`、绑定当前 manifest 的版本与 hash、`outputHash` 等于输出文件当前内容的 sha256。工作台生成与 checker 调用同一个判断函数。

| 动作 | 允许的起始状态 | 前提 | 结果 |
|---|---|---|---|
| 初始化 | — | — | 全部 unit 为 `not-started` |
| 生成 unit（页面按钮或 `POST /api/generate` 带 `unitId`） | 任意 | 当前阶段为 Phase 4；G1–G3 最新记录为 approved；`tokens/src` 里已有 token 文件；所有依赖满足；该工作区没有正在运行的任务 | unit 变为 `in-progress`；所有直接或间接依赖它、且不是 `not-started` 的下游 unit 变为 `changes-requested`，因为它们基于旧的上游产出 |
| 生成成功 | `in-progress` | 生成进程只改动了该 unit 自己的目录 | `in-review`，随即自动启动审查 |
| 生成失败、超时，或改动了该 unit 目录以外的文件 | `in-progress` | — | 越界改动的文件恢复原样；unit 保持 `in-progress`，`note` 写明原因 |
| 自动审查完成 | `in-review` | 审查期间状态与输出内容都没有变化 | 追加 unit-review；unit 变为 `approved` 或 `changes-requested` |
| 自动审查失败或结论无效 | `in-review` | — | 不追加记录；unit 保持 `in-review`，`note` 写明原因；可以只重新审查 |
| 输出在审查之后被改动（自动推进或“只重新审查”时处理） | `approved` | 输出文件存在；最新审查绑定当前 manifest，但 `outputHash` 与当前输出不同。manifest 变了说明规格变了，不适用这一行，按生成规则重做 | unit 变为 `in-review`，`metadata.json` 改绑当前输出的 hash；直接或间接依赖它、处于 `approved` 的下游 unit 中，有输出的变为 `in-review`（它们的审查对照的是旧的上游产出），没有输出的变为 `changes-requested`。只重新审查而不重新生成，手工修改不会被覆盖 |
| 只重新审查（`POST /api/review`） | `in-review`，或按上一行转为 `in-review` 的 `approved` unit | 当前阶段为 Phase 4；所有依赖满足；没有正在运行的任务 | 重新运行自动审查 |
| 外部 agent 记录审查（`POST /api/approve`，`kind=unit-review`） | `in-review` | reviewer 为有名字的 subagent；`fileScope` 只引用该 unit 的输出文件；`outputHash` 等于输出文件当前内容 | 追加 unit-review；unit 变为 `approved` 或 `changes-requested` |

#### Phase 4 自动推进

Phase 4 的主按钮（或 `POST /api/generate` 只带 `phase: 4`、不带 `unitId`）启动一次自动推进：一个任务从头到尾占住工作区，按下表把所有 unit 和平台资产做完，停在 G4 前。逐个 unit 的按钮和接口保留，用来单独重做某个 unit。

自动推进不需要中途确认，因为 unit 的通过与否本来就由独立审查进程决定，用户在这一阶段真正要做的判断只有 G4。G4 仍由用户在资产审阅页确认，自动推进从不记录它。

“已通过”的 unit：状态为 `approved`，且最新一条 unit-review 结论为 `approved`、仍绑定当前 manifest 与当前输出（与依赖满足用同一个判断）。

| 情形 | 动作 |
|---|---|
| 启动 | 前提与生成单个 unit 相同：当前阶段为 Phase 4；G1–G3 最新记录为 approved；`tokens/src` 里已有 token 文件；没有正在运行的任务 |
| 选下一个 unit | 按 manifest 顺序取第一个未通过的 unit；它的依赖必须已满足，否则停下并说明缺哪些依赖 |
| 该 unit 处于 `in-review`，或处于 `approved` 但输出在审查之后被改动 | 只运行审查，不重新生成；后者先按“unit 推进规则”转为 `in-review`，连同已通过的下游 unit |
| 其他未通过的情况（`not-started`、`in-progress`、`changes-requested`，以及不属于上一行的 `approved`：输出缺失、manifest 已变化、没有对应的审查） | 按单个 unit 的规则生成并审查；重新生成时 prompt 带上最近的审查意见 |
| 审查结论 `approved` | 进入下一个 unit |
| 审查结论 `changes-requested`，本次推进中该 unit 生成未满 3 次 | 带着审查意见重新生成并审查 |
| 审查结论 `changes-requested`，本次推进中该 unit 已生成 3 次 | 停下；unit 保持 `changes-requested`，任务报告该 unit 和最后一次审查的摘要 |
| 生成失败、超时、改动 unit 目录以外的文件，或审查失败 | 停下；unit 的状态与 `note` 按单个 unit 的规则记录 |
| 所有 unit 已通过 | 若 `project/status.json` 的 `state` 不是 `in-review` 或该文件未通过检查，或 Phase 4 必需文件有缺失，或 checker 在整阶段生成任务可以改动的文件里发现空文件、失效链接，按整阶段生成任务的规则生成平台资产；否则跳过并在日志说明。检查里的其他问题（更早阶段缺失的文件、brief、manifest、审批，以及 `project/approvals.json`、`src/ui/ir/`、`src/ui/units/` 里的文件，整阶段任务对它们的改动会被恢复）重新生成资产也修不好，所以不触发重新生成 |
| 平台资产生成失败或改动受保护文件 | 停下，按整阶段生成任务的规则恢复和报告 |
| 平台资产完成或跳过 | 运行 Phase 4 检查，结果与任务一起返回；检查通过时页面显示资产审阅页和 G4 确认按钮，未通过时显示检查输出，按钮回到“继续自动生成 Phase 4”；任务结束和重新打开页面走同一个判断：状态为 `in-review` 且所有 unit 已通过时运行检查，通过才显示 G4，否则显示检查输出和“继续自动生成 Phase 4”；状态不是 `in-review` 时显示自动生成按钮。在本页面停下的任务（出错或达到重做上限）不显示 G4，只显示原因和“继续自动生成 Phase 4” |

停下后再次启动会从第一个未通过的 unit 接着做，已通过的 unit 和已生成的平台资产不重做；3 次的上限按每次启动重新计。先做 unit 再做平台资产，与阶段说明“按工作单元生成产品界面，再生成平台资产”的顺序一致。

#### 生成与审查用哪个 AI

`config/ui.json` 的可选字段 `agents` 分别设置 UI 工作单元的生成（`generation`）与审查（`review`），每项可填：

- `engine`：`codex`（默认）或 `claude`（Claude Code）。
- `model`：模型名，留空则跟随该引擎自己的默认配置（codex 读 `~/.codex/config.toml`，Claude Code 读它自己的设置）。只允许字母、数字和 `. _ : / [ ] -`，且不能以 `-` 开头。
- `reasoningEffort`：推理强度，留空跟随引擎默认。codex 可选 `low`、`medium`、`high`；Claude Code 可选 `low`、`medium`、`high`、`xhigh`、`max`。

创建工作区时在页面上选填，之后可以直接编辑 `config/ui.json`；对已有工作区再次初始化并传入 `agents` 时，会写进现有的 `config/ui.json`。整阶段生成任务（各阶段的“生成”按钮）不受这项设置影响，始终用 codex 默认配置。推荐生成用 codex、审查用 Claude Code：换一家的模型来审，最能发现生成模型自己的盲区。

两种引擎都被限制在同样的范围内：

| | codex | Claude Code |
|---|---|---|
| 生成 | `codex exec -s workspace-write`：系统沙箱只允许写工作区和系统临时目录（`$TMPDIR`、`/tmp`） | `claude -p --permission-mode dontAsk`，只放开 Read、Glob、Grep，以及对该 unit 目录的 Write、Edit；其他写入和 Bash 都被拒绝 |
| 审查 | `codex exec -s read-only`，`--output-schema` 约束结论，`-o` 写入 `review.json` | 同样的权限但不放开 Write、Edit，`--json-schema` 约束结论，工作台把返回的 `structured_output` 写入 `review.json` |

Claude Code 的权限规则会把路径里的 `* ? [ ] { } ( )` 当作匹配符号，所以工作区路径含这些字符时拒绝用 Claude Code 生成；unit id 只能用小写字母、数字和连字符，因为它同时决定 unit 目录和这条写权限。两者事后都再经过下面的越界核对。Claude Code 即使出错也可能以退出码 0 结束，所以工作台读取它输出里的 `is_error` 判断成败。每条审查记录的 `reviewer` 写明 `engine`、实际用的 `model`（Claude Code 取自它返回的用量信息；codex 取设置值，没设时取 `~/.codex/config.toml` 的默认值）和 `reasoningEffort`；unit 的 `metadata.json` 记录生成它的引擎与模型。

自动审查由工作台另起一个只读的审查进程，按 `assets/unit-review-verdict.schema.json` 输出结论并写入 `src/ui/units/<unitId>/review.json`；它与生成进程是两个独立进程，不复用生成时的上下文。生成 unit 时使用只针对该 unit 的 prompt，进程只能写工作区：生成与审查都不加 `--add-dir`，因为加进去的目录会变成可写，技能目录的参考文档只按绝对路径读取。重新生成时，prompt 带上该 unit 最近一条“要求修改”审查的全部证据和状态备注，生成进程据此逐条修改，而不是重复上一次的输出。进程结束后（包括失败和超时），工作台先核对 `brand.brief.json`、`project/`、`config/`、`tokens/`、`src/ui/ir/` 与其他 unit 目录有没有被改动，有就恢复原样，再报告结果；恢复本身出错时，错误信息会写明这些文件可能仍被改动。进程结束或超时后，工作台都会结束它启动的全部子进程（包括放到后台的命令），再开始核对；关闭工作台时，仍在运行的生成和审查进程也一并结束。恢复前，被改动的版本先另存到 `~/.cache/brand-system/restore-*`（设置了指向系统临时目录以外的绝对路径 `XDG_CACHE_HOME` 时用它下面的 `brand-system/`），错误信息写明保存位置，所以在任务运行期间用其他程序改了这些文件也不会丢。这些备份不会自动删除，确认不再需要后可以手动删掉。这个目录也放审查结论、Claude Code 输出和 Chrome 截图配置的临时文件，它们用完即删；之所以不放系统临时目录，是因为 codex 沙箱可以写那里。

整阶段生成任务（各阶段的“生成”按钮）以 `codex exec -s workspace-write -C <工作区>` 运行：系统沙箱只允许写工作区和系统临时目录，技能目录的参考文档按绝对路径只读。它可以写工作区里的大多数文件，但不能改 `project/approvals.json`、`src/ui/ir/`、`src/ui/units/` 和 `status.json` 里的 `units`：这些属于审批和 unit 流程，改动会在任务结束（包括失败和超时）后恢复原样，并判为失败。

生成任务的状态只反映生成（unit 任务还包括自动审查）是否成功。阶段检查的结果单独返回并显示在页面上：Phase 4 在所有 unit 审查完之前检查本来就不会通过，这不等于生成失败；推进阶段时仍以检查通过为前提。

gate 是否已批准，工作台与 checker 用同一个函数判断（`check_workspace.latest_gate_states`）：缺字段的 approved 记录不算批准，`kind` 写错的记录会撤回它指向的 gate。

所有对 `project/status.json`、manifest 与 `project/approvals.json` 的读改写都在同一把锁内完成，同一工作区同一时间只运行一个任务；任务运行期间，记录审批与推进阶段的请求一律拒绝，否则 unit 任务结束时的越界检查会把这期间的合法写入当成越界改动恢复掉。
