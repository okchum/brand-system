## 23. 审阅页面、品牌手册与设计工具交接

每阶段建立能实际打开的审阅页，优先静态可分发 HTML/CSS/JS；若依赖本地服务器，提供经过验证的启动方法，不谎称双击即可使用。

页面包含当前版本、阶段、批准状态、适用范围、方案比较、真实素材、真实 token 值、说明和已生成文件下载。未生成资产显示 pending，而不是假下载按钮。

提供亮/暗切换、尺寸预览、可复制颜色/变量、素材分类、搜索或清晰导航、使用/禁用例。让人能看清设计，不为品牌手册堆砌无关功能。

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
- `kind: "unit-review"`：必须带 `unitId`、`manifestVersion`、`manifestHash`、`outputHash`、`fileScope`、`reviewer`、`conclusion`、`evidence` 和 status。`outputHash` 是被审查输出文件内容的 `sha256:<hex>`，输出被重新生成或改动后旧记录不再算数；`fileScope` 是一个或多个 `{path, startLine, endLine}`，`path` 只能是该 unit 在 manifest `files` 中列出的输出文件；`reviewer.type` 固定为 `subagent`，`reviewer.name` 不能为空；status 与 conclusion 相同，只能是 `approved`、`changes-requested`。

每个 unit 都必须有自己的 `unit-review` 记录，并绑定同一份 manifest 的版本与 SHA-256 hash，以及被审查输出内容的 `outputHash`。evidence 必须是非空数组，内容可以是测试命令、静态检查结果、差异摘要或证据文件路径。当前模型为 GPT 时，审查 subagent 只能继承当前模型，不得调用 sonnet、opus、fable、haiku 等 Claude 模型；reviewer 记录角色和名称即可，不写模型选择字段。
