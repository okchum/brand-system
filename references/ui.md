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

`src/ui/ir/manifest.json` 是页面和组件工作单元的唯一清单，必须包含 `manifestVersion`、`hash` 和 `units`。每个 unit 必须包含 `id`、`kind`、`status`、`files`、`platforms`；`kind` 只能使用 `page-map`、`layout`、`component`、`page`、`platform-adaptation`。`status` 只能是 `in-progress`、`in-review`、`approved`、`changes-requested`、`completed`。

工作顺序固定为：页面地图 → 布局 → 复用 → 组件 → 页面 → 平台适配；复用是组件设计前的强制步骤，不单独增加 unit kind。每个 unit 都必须启动独立 subagent review；审阅记录写入 `project/approvals.json` 的 `kind=unit-review`，不能用一次总评替代逐单元记录。
