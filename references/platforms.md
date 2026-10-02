## 15. Web、PWA、App 与桌面图标

### 15.1 平台规格集中管理

创建 `config/platforms.json`，每个平台记录官方资料、核验日期、目标版本/用途、尺寸、文件格式、透明度、裁切/安全区、色彩配置、生成入口和验证状态。

尺寸与平台要求可能改变。以下是要检查的种类，不是永远正确的硬编码上传规格。实际导出以本次核验结果为准；无法核验则标注。

### 15.2 Web/PWA

考虑 standalone favicon.svg、多分辨率 favicon.ico、常用小尺寸 PNG、Apple touch icon、PWA any/maskable 图标与 monochrome/pinned-tab（适用时）。

为 maskable 设计实际安全区，不只是把普通图标改名。检查方形、圆形、圆角和不同浏览器标签页背景。

提供真实可复制的 HTML head、manifest 和框架适配示例。所有路径对应实际文件；域名未确认时保留明确待配置项，不装作可部署完成。

### 15.3 Apple 与 Android

保留品牌核心几何，同时允许平台独立构图和图层。

Apple：核对目标平台是否需要分层资源、不同外观或系统处理；按当前工具链正确准备。不得把一张 1024 PNG 宣称为所有 Apple 平台完整原生包。

Android：核对 adaptive foreground/background、monochrome/themed、legacy、商店与通知图标需求；不同用途可能需要不同处理，不能都塞完整彩色字标。

### 15.4 Desktop 与扩展

按范围提供 macOS、Windows、Linux 的图标源与导出；考虑菜单栏/托盘单色图标、深浅环境、高 DPI、安装器、任务栏和扩展市场展示。

`.icns`、`.ico` 等文件必须是真实生成且可解析的对应格式，不通过重命名 PNG 冒充；用 `<skill>/scripts/icon_verify.py` 按文件头核对格式与尺寸。

平台所需专用工具不可用时，交付可验证的源文件、生成配方与待办，并标为 `source-ready / platform-validation-pending`，不写成平台正式可用。

启动页以平台规范和易用性为先，不制作人为拖慢启动的品牌广告动画。
