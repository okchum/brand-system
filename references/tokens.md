## 11. 布局、设计变量与工程接口

### 11.1 唯一权威来源

`tokens/src/` 是设计变量的权威来源。生成 CSS、TypeScript、JSON 快照、设计工具适配文件与文档色板，禁止分别手填同一组值。

使用明确版本的格式规范。采用 DTCG 时，核对选定版本的类型、颜色对象、dimension 单位、引用与组合值规则，并运行匹配的验证器。不能只因 JSON 有 `$type`/`$value` 就宣称全面兼容；也不要把社区组规范称作 W3C Recommendation。

允许采用带 schema 的内部规范，但必须说明与 DTCG 的差异和转换方式，不作虚假兼容声明。

### 11.2 覆盖类别

color、typography、spacing、size、radius、border、elevation/shadow、opacity、motion、z-index、breakpoint、container、icon-size、density。

采用少而一致的基础步长；光学校正可作为具名例外，不因“禁止 magic number”而牺牲视觉质量。品牌身份与不同设备的布局值可以有独立层级。

支持 `light / dark / system`；需要时加入舒适/紧凑密度。不要仅用缩小字体制造紧凑布局。

### 11.3 引用与生成

primitive → semantic → component 是变量依赖层级，不代表 Logo 几何必须依赖组件变量。

整个系统是依赖图：品牌策略影响颜色、字体、Logo 几何、语言；这些再影响主题、模板、组件与平台适配，最后产生发布输出。

验证循环引用、缺失引用、类型冲突、同名冲突、主题缺值、生成偏差与弃用项。相同数值本身不是错误，不把多个合理 alias 当作失败。

### 11.4 开发者使用

提供 CSS custom properties 和 TypeScript/JSON 消费示例。按现有栈选择是否提供组件适配层，不同时堆砌 React/Vue/Svelte/Angular 实现。

确需原生 token 导出时，再实现 Swift/Kotlin 等对应转换并验证；文档样例不等于已完成原生集成。

提供命名空间、包入口、类型、tree-shaking 友好导出（适用时）、版本与兼容策略。不同品牌或子产品不得通过随意覆盖全局变量造成污染。

### 11.5 UI 契约中的 token 引用

`tokens/src/` 是唯一 token SSOT。`config/ui.json` 只能用固定的 `tokenSource: "tokens/src"` 指向它，不能在 UI config、IR manifest 或 unit review 中复制颜色、间距、字体等 token 值。`html-css-js` 与 `react` 是初始 stack profile；没有对应工具链时，交付状态只能为 `preview-only` 或 `handoff-ready`，文档和审阅记录不得声称 native runtime 已实现。

UI unit 的输出页面不自己加载 token：工作台打开 `src/ui/` 下的页面时，把 `tokens/src/` 展开成 CSS 变量插进 `<head>`。叶子是带 `$value` 或 `value` 的对象，变量名是它的 JSON 路径用 `-` 连接（`color.light.canvas` → `--color-light-canvas`，`space.4` → `--space-4`），值里的 `{a.b}` 引用变成 `var(--a-b)`。页面只写 `var(--…)`，不 fetch token 文件，不重新声明这些变量，也不内联数值；这样 token 值仍只在 `tokens/src/` 一处，而沙箱里的预览照样有样式。
