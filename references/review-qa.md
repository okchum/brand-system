## 23. 审阅页面、品牌手册与设计工具交接

每阶段建立能实际打开的审阅页，优先静态可分发 HTML/CSS/JS；若依赖本地服务器，提供经过验证的启动方法，不谎称双击即可使用。

页面包含当前版本、阶段、批准状态、适用范围、方案比较、真实素材、真实 token 值、说明和已生成文件下载。未生成资产显示 pending，而不是假下载按钮。

提供亮/暗切换、尺寸预览、可复制颜色/变量、素材分类、搜索或清晰导航、使用/禁用例。让人能看清设计，不为品牌手册堆砌无关功能。工作台预览把页面放在沙箱里打开（独立的空白来源），所以审阅页要自包含：页面需要的数据（例如选定方向、审批结论）在生成时写进页面，样式、脚本和图片内联或用 data URI，不用 fetch、XMLHttpRequest、localStorage 或 cookie 在运行时读取（UI unit 页面所需的 token 由工作台注入，见 tokens.md「UI 契约中的 token 引用」）；亮/暗切换这类交互用页面内状态即可。可以用 `<a href>` 相对路径链接工作区里的其他文件，例如 `../reports/qa-report.md`：工作台按文件路径提供预览，直接打开文件时链接也能用。样式、脚本、图片、字体不按相对路径加载工作区文件：沙箱里的页面加载字体和模块脚本时会被工作台拒绝，统一内联最可靠。checker 检查审阅页的脚本没有上述运行时读取、没有按相对路径加载工作区文件，且每个相对 `<a href>` 都指向工作区里存在的文件（按工作台的方式解析符号链接）。正文里提到这些词、`<pre>` 里的代码示例不算。

最终品牌手册包含策略、声音、Logo、色彩、字体、布局、图标、图形、插画、动效、图表、UI、平台、营销、邮件、印刷、许可和版本。品牌手册自身也使用同一套 tokens。

生成 contact sheet 便于检查所有 Logo 和图标，提供真实尺寸预览与合理放大的细节；标注显示比例。

设计工具交接：

- 可以交付 SVG、tokens、排版规范、模板源码和重建说明。
- 只有在实际创建并验证原生 Figma/Sketch/Illustrator 等文件时才列入交付。
- SVG 可导入不等于已经存在完整可编辑的组件库、自动布局和变量绑定。
- 不伪造 `.fig`/`.ai` 文件，不把重命名或嵌入截图包装成原生设计源。
- 没有设计工具集成时，明确原生组件库是待完成项目；现有源文件仍应可用于后续交接。

## 24. QA：结构、视觉、交互与生产验证

创建 `reports/qa-report.md` 和机器可读结果。每项记录测试对象、方法、环境、命令、结果、证据路径、限制与修复建议。

### A. 完整性与来源

检查 required 范围、真实文件、manifest 引用、命名、版本、依赖图、许可、未知占位值、空文件和损坏文件。所有未完成项清晰显示，不通过填空文件凑“100%”。

### B. 矢量与输出

检查 XML/viewBox、裁切、边界、尺寸、透明度、无意外栅格、外链和脚本；发布字标的字体依赖、inline SVG 的 ID 冲突、图标平台格式和 MIME 一致性。

渲染前后对比，确保优化没有改变形状或颜色。检查浅深背景、单色、16/20/24/32 px 与大尺寸，不能只测试文件能解析。

### C. Tokens

检查 schema、类型、引用、主题覆盖、循环、未使用项提示、生成同步和需要适配器的格式差异。合理的相同值与 alias 不当成错误。

### D. 无障碍与界面

对真实使用的颜色组合和状态测试；验证基础键盘操作、可访问名称、焦点、缩放、重排和 reduced motion。不能仅靠自动扫描宣布整体 WCAG 合规。

### E. 模板与排版

检查多语言、长标题、极短/极长内容、缺图、数字、换行、字体 fallback、截图裁切、邮件纯文本与导出尺寸。所有营销/商务演示数据不能被误认为真实事实。

### F. 重建与回归

从独立干净输出目录重建，验证所需环境依赖及产物清单；同环境比较关键文件哈希或合理的视觉差异。新截图不是自动通过基线，基线变化须人工审阅。

### G. 人工/外部检查

单列审美审阅、Logo 辨识、真实设备、原生打包、邮件客户端、商标核查、印刷打样与供应商工艺。没有执行的就是未执行，不用脚本通过代替。

问题级别：blocker、major、minor、observation。不得为了“全绿”修改测试目标或删除失败检查。客观工具不支持的项目标 blocked 并解释；核心 required 阻塞未解决前只能交付候选版本。

### H. UI unit review 契约

`project/approvals.json` 是追加式记录，记录必须带 `kind`：

- `kind: "gate"`：沿用 gate、status、scope、snapshot、confirmation、approvedAt，以及 version 或 hash；gate 只能是 `G1`–`G5`，status 只能是 `pending`、`approved`、`changes-requested`。
- `kind: "unit-review"`：必须带 `unitId`、`manifestVersion`、`manifestHash`、`outputHash`、`fileScope`、`reviewer`、`conclusion`、`evidence` 和 status。`outputHash` 是 `sha256:<hex>`，按 manifest `files` 的顺序对每个文件依次喂入「相对路径、NUL、文件字节、NUL」后求得（不等于单个文件内容的 sha256），由工作台计算并写入 `metadata.json`，输出被重新生成或改动后旧记录不再算数；`fileScope` 是一个或多个 `{path, startLine, endLine}`，`path` 只能是该 unit 在 manifest `files` 中列出的输出文件；`reviewer.type` 固定为 `subagent`，`reviewer.name` 不能为空，工作台的自动审查还会写明 `engine`、实际使用的 `model` 和 `reasoningEffort`；status 与 conclusion 相同，只能是 `approved`、`changes-requested`；工作台的自动审查按列出的问题的严重度记录结论（有 P0/P1 即 `changes-requested`，只有 P2/P3 即 `approved`；一个问题都没列时保留审查进程自己的结论），与审查进程原结论不符时以严重度为准，并记入日志和 evidence，`review.json` 保留审查进程的原文；可选的 `summary` 是一句话结论，工作台直接显示它；工作台写入的记录还带 `reviewedAt`（UTC 时间）。gate 的撤回记录用 `requestedAt` 记录时间，不带 `approvedAt`。

Phase 4 及以后（含发布检查）每个 unit 都必须处于 `approved`，且有自己的、仍对应当前输出的 approved `unit-review` 记录，并绑定同一份 manifest 的版本与 SHA-256 hash，以及被审查输出内容的 `outputHash`。evidence 必须是非空数组，内容可以是测试命令、静态检查结果、差异摘要或证据文件路径。主 agent 自己派审查 subagent 时，若当前模型为 GPT，subagent 只能继承当前模型，不得调用 sonnet、opus、fable、haiku 等 Claude 模型；工作台的自动审查按 `config/ui.json` 的 `agents` 选择引擎和模型，并把它们写进 reviewer。

一个 unit 自上次 approved 以来已有 `changes-requested` 审查时，下一次审查是复审：逐条核对最近几次这样的审查（次数见 `scripts/workbench.py` 的 `REVIEW_ROUNDS_CARRIED`）列出的问题是否修好（更早的轮次不再带入，以免 prompt 无限变长），未修好的、以及这次修改新引入的问题照常定级；复审才第一次看到、在上一版就已存在的问题最多记 P2。全量重查每轮都能在大页面上找出新问题，复审保证 unit 能在有限轮次内收敛；代价是首轮漏掉的既有问题不会再阻塞 unit，而是作为 P2 留在记录里。

只读源码看不出整体视觉问题（例如固定高度的侧栏在页面下半段断开、元素重叠或溢出），所以工作台在每次 unit 审查前用无头 Chrome 渲染注入 token 后的输出，按 `scripts/workbench.py` 的 `SCREENSHOT_VIEWS` 中的桌面与手机尺寸各截页面顶部和滚到底部两张，存进 unit 目录的 `screenshots/`，交给审查进程一起看；视口用真实高度，否则 `100vh` 会被撑满而把这类问题藏起来。找不到 Chrome 时照常审查，但日志写明本次没有视觉截图。
