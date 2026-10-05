## 20. 目录与来源关系

阶段必需文件及审批依赖的机器可读权威来源为 `<skill>/config/phase_requirements.json`；下列目录树补充条件模块与用途，不另定义强制文件清单。可以适配现有仓库，但必须保持以下职责清晰。以下是目标结构，不是声称这些文件已存在。方括号是**最早应创建的阶段**：只创建当前阶段及之前标注的文件，不提前铺设，不用空目录冒充交付。

```text
brand-workspace/
├── AGENTS.md / CLAUDE.md            [0] 短规则/导航；已有时安全合并，不覆盖
├── brand.brief.json                 [0] 用户输入/经确认的 brief（模板见 `<skill>/assets/brief.template.json`）
├── README.md                        [0]
├── BRAND_SYSTEM.md                  [2] 设计系统总览，数值引用权威来源
├── CHANGELOG.md                     [5]
├── THIRD_PARTY_NOTICES.md           [3] 首次引入第三方字体/图标/依赖时
├── project/
│   ├── plan.md                      [0] 里程碑及验收条件
│   ├── status.json                  [0] 进度与 blockers 的唯一权威来源
│   ├── approvals.json               [0] 审批记录的权威来源
│   ├── decisions.md                 [0] 简要设计与工程决策，不要求内部思维过程
│   └── handoff.md                   [0] 下次启动所需摘要，不覆盖 status 的事实
├── docs/
│   ├── scope-matrix.md              [0]
│   ├── assumptions.md               [0] 仅在使用暂定条件时
│   ├── environment.md               [0]
│   ├── references.md                [0]
│   ├── strategy.md                  [0]
│   ├── voice.md                     [0]
│   ├── logo.md                      [2]
│   ├── color.md                     [2]
│   ├── typography.md                [2]
│   ├── accessibility.md             [3]
│   ├── platform-specs.md            [4]
│   ├── licensing.md                 [4]
│   └── handoff-by-role.md           [5]
├── config/
│   ├── brand.json                   [2] 生产设置；不重复维护完整 brief 与 token 值
│   ├── quality.json                 [3]
│   ├── platforms.json               [4]
│   └── exports.json                 [4]
├── tokens/                          [3]
│   ├── src/
│   │   ├── primitives/
│   │   ├── semantic/
│   │   └── components/
│   └── schema/
├── masters/
│   ├── logo/                        [2]
│   ├── symbol/                      [2]
│   ├── graphics/                    [2]
│   ├── illustrations/               [4]
│   ├── photography/                 [4] 仅在有真实授权资源时
│   └── platform-compositions/       [4]
├── templates/                       [4]
│   ├── web/
│   ├── social/
│   ├── email/
│   ├── presentations/
│   └── print/
├── src/
│   ├── preview/                     [1] 审阅与品牌手册的可维护源
│   ├── components/                  [3] 按已批准技术栈
│   └── generators/                  [4]
├── scripts/                         [2]
├── tests/                           [3]
├── manifests/
│   └── assets.source.json           [4] 人维护的资产意图/来源元数据
├── review/                          [1] 已生成审阅入口；审批快照另行保留
├── reports/                         [1] 当前测试结果、证据和历史快照
└── dist/                            [5] 可重建的发布输出，禁止手改
    ├── tokens/
    ├── assets/
    ├── brand-book/
    ├── brand-manifest.json          从源清单和构建结果生成
    ├── licenses/
    └── packages/
```
不要同时在 masters、assets、dist 中手工维护三份等价 Logo。设计母版由人批准；衍生图由脚本生成。

文档包含设计理由与使用规则；具体色值、路径、尺寸等能生成的表格应从权威配置生成，避免文档值与代码值漂移。

素材名称采用 lowercase-kebab-case，包含必要的品牌、用途、变体、主题、尺寸。如 `brand-symbol-mono-white.svg`。版本由包与 manifest 管理，不靠 `final-final-v7.svg`。

## 21. 资产清单、状态和授权

每个重要资产在 manifest 中记录：

`id / category / role / lifecycle / verification / sourcePaths / dependencies / outputs / format / dimensions / theme / platform / locales / usage / license / version / hash`

三个维度必须分开：

- `role`：master、template、generated。
- `lifecycle`：draft、in-review、approved、deprecated。
- `verification`：not-run、passed、failed、blocked、manual-review-required。

需要时为每项验证单独存状态。批准不意味着每一项验证都 passed；生成不意味着已经 approved。

尚未生成的输出只能出现在 expected outputs 中，不能混进真实存在的发布文件清单。文件路径、文件数和哈希应从磁盘获得，不凭空填写。

保持依赖图无环；修改主色、字标或尺寸配置后能够找到受影响的衍生文件。

### UI 契约文件与状态字段

产品 UI 的契约文件由后续实现创建：`config/ui.json` 描述 `version`、平台数组、`stackProfile`、固定的 `tokenSource: "tokens/src"` 和 `deliveryStatus`；`src/ui/ir/manifest.json` 描述 `manifestVersion`、`hash` 与 unit 清单。两者的字段定义以 `assets/brief.schema.json` 的 `$defs.uiConfig` 与 `$defs.irManifest` 为准，不在其他文档中另造字段。

`project/status.json` 的 `units` 字段是数组，每项只有 `unitId`、`status`，可选 `updatedAt` 与 `note`；status 复用 `$defs.unitStatus` 的有限集合 `not-started`、`in-progress`、`in-review`、`approved`、`changes-requested`；manifest 不记录进度。`project/approvals.json` 每条记录必须带 `kind`，只能是 `gate` 或 `unit-review`。unit review 必须绑定 unitId、manifestVersion、manifestHash、被审查输出的 outputHash、文件范围、subagent reviewer、结论和非空证据；完整字段以 `$defs.unitReviewApproval` 为准。

第三方图标、照片、字体引用与依赖记录来源及许可。品牌资产、第三方资源和生成脚本可以有不同许可；不要因为构建代码开源，就擅自把品牌标志置于开源许可或公有领域。

发布包不包含字体二进制、密钥、用户原始私密资料、缓存或无关参考图片。不要删除第三方必要署名和许可证通知。

## 22. 自动生成与可重建性

实现一套实际可运行的命令接口，采用现有项目的包管理器。以下为项目自定义命令名，不是 agent 或工具预置的命令：

```text
brand:dev             本地可视化预览
brand:build           从权威源生成当前阶段所有适用衍生物
brand:tokens          校验并生成变量输出
brand:export          按配置生成 Logo、图标、卡片和模板导出
brand:validate        自动结构/格式/对比度/维度等检查
brand:test            单元、集成和可运行交互检查
brand:preview-build   构建可分发品牌手册
brand:package         创建发布包及发布清单
brand:check           聚合当前阶段强制检查，失败返回非零退出码
```

README 必须给出实际可执行的完整命令。尚未实现的命令不得写成可运行。

实现要求：

- 锁定直接依赖与可复现安装方式，记录关键渲染器、操作系统及字体条件。
- 从母版直接按目标尺寸渲染，不用低分辨率 PNG 放大到大尺寸。
- 使用与用途匹配的颜色空间、透明度与编码；无必要不生成体积巨大的文件。
- 模板的文字溢出、缺图、缺字体、长标题和多语言必须有检测或明确处理。
- 对内容输入、文件路径和 SVG 外部资源做适当校验，不将任意内容直接拼到命令或 HTML 里。
- 固定随机 seed；可复现产物不嵌入随机时间戳。必要运行时间写到独立构建报告。
- 先输出临时目录，验证成功再替换发布输出；构建失败不覆盖已批准的有效发布包。
- 清理仅作用于受控生成目录，不使用可能删除仓库外文件的命令。
- 同环境、同输入尽量得到相同结果；跨平台字体栅格化等已知差异必须记录，不能承诺任意机器二进制完全相同。
- 审批绑定的审阅快照保留，不在下一次构建中悄悄覆盖。

在临时工作副本执行一次“改变一个 token → 构建 → 检查所有受影响输出 → 恢复”的验证，证明来源传播有效。不要把测试中的临时改色作为批准品牌提交。
