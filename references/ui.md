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

`src/ui/ir/manifest.json` 是页面和组件工作单元的唯一清单，必须包含 `manifestVersion`、`hash` 和 `units`。每个 unit 必须包含 `id`、`kind`、`status`、`files`、`platforms`；`kind` 只能使用 `page-map`、`layout`、`component`、`page`、`platform-adaptation`。`status` 只能是 `not-started`、`in-progress`、`in-review`、`approved`、`changes-requested`、`completed`。

工作顺序固定为：页面地图 → 布局 → 复用 → 组件 → 页面 → 平台适配；复用是组件设计前的强制步骤，不单独增加 unit kind。每个 unit 都必须启动独立 subagent review；审阅记录写入 `project/approvals.json` 的 `kind=unit-review`，不能用一次总评替代逐单元记录。

依赖只认 manifest 每个 unit 的 `dependsOn`：初始化按上面的顺序写入，之后工作台生成与 checker 都读同一份。`files` 在初始化时就写定为 `src/ui/units/<unitId>/output.html`，之后不再改写：hash 覆盖 `files`，推进过程中再填写会让所有已绑定的 review 失效。

#### unit 推进规则

进度只认 `project/status.json` 的 `units`；manifest 里的 `status` 由同一次写入同步，不作为判断依据。

依赖满足：依赖 unit 处于 `approved` 或 `completed`，且它最新一条 unit-review 结论为 `approved`、绑定当前 manifest 的版本与 hash、`outputHash` 等于输出文件当前内容的 sha256。工作台生成与 checker 调用同一个判断函数。

| 动作 | 允许的起始状态 | 前提 | 结果 |
|---|---|---|---|
| 初始化 | — | — | 全部 unit 为 `not-started` |
| 生成 unit（页面按钮或 `POST /api/generate` 带 `unitId`） | 任意 | 当前阶段为 Phase 4；G1–G3 最新记录为 approved；所有依赖满足；该工作区没有正在运行的任务 | unit 变为 `in-progress`；所有直接或间接依赖它、且不是 `not-started` 的下游 unit 变为 `changes-requested`，因为它们基于旧的上游产出 |
| 生成成功 | `in-progress` | 生成进程只改动了该 unit 自己的目录 | `in-review`，随即自动启动审查 |
| 生成失败、超时，或改动了该 unit 目录以外的文件 | `in-progress` | — | 越界改动的文件恢复原样；unit 保持 `in-progress`，`note` 写明原因 |
| 自动审查完成 | `in-review` | 审查期间状态与输出内容都没有变化 | 追加 unit-review；unit 变为 `approved` 或 `changes-requested` |
| 自动审查失败或结论无效 | `in-review` | — | 不追加记录；unit 保持 `in-review`，`note` 写明原因；可以只重新审查 |
| 只重新审查（`POST /api/review`） | `in-review` | 当前阶段为 Phase 4；没有正在运行的任务 | 重新运行自动审查 |
| 外部 agent 记录审查（`POST /api/approve`，`kind=unit-review`） | `in-review` | reviewer 为有名字的 subagent；`fileScope` 只引用该 unit 的输出文件；`outputHash` 等于输出文件当前内容 | 追加 unit-review；unit 变为 `approved` 或 `changes-requested` |

自动审查由工作台另起一个只读的 `codex exec`（`-s read-only`），按 `assets/unit-review-verdict.schema.json` 输出结论并写入 `src/ui/units/<unitId>/review.json`；它与生成进程是两个独立进程，不复用生成时的上下文。生成 unit 时使用只针对该 unit 的 prompt，进程只能写工作区，结束后工作台核对 `project/`、`src/ui/ir/` 与其他 unit 目录没有被改动。

生成任务的状态只反映生成（unit 任务还包括自动审查）是否成功。阶段检查的结果单独返回并显示在页面上：Phase 4 在所有 unit 审查完之前检查本来就不会通过，这不等于生成失败；推进阶段时仍以检查通过为前提。

所有对 `project/status.json`、manifest 与 `project/approvals.json` 的读改写都在同一把锁内完成，同一工作区同一时间只运行一个任务；任务运行期间，记录审批与推进阶段的请求一律拒绝，否则 unit 任务结束时的越界检查会把这期间的合法写入当成越界改动恢复掉。
