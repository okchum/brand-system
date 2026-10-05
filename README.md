# brand-system

一个 agent skill：为软件产品建立生产导向的完整品牌系统仓库——策略、三套视觉方向、Logo 矢量母版、design tokens、产品 UI 规范、平台图标、营销/邮件/演示/印刷模板、自动导出、文档与 QA。分六个阶段推进，每阶段停在显式审批门。阶段文件清单与审批依赖以 `config/phase_requirements.json` 为唯一机器可读来源。

## 结构

```text
SKILL.md        流程、审批门、禁令、首次执行——每次调用都加载
references/     各模块细则，按阶段读取（读取表在 SKILL.md 开头）
assets/         brief 模板与 JSON Schema
scripts/        确定性检查与单文件构建，只依赖 Python 3.9 标准库
evals/          虚构示例 brief，用于试跑阶段 0–1
tests/          scripts 与包结构的测试
config/         阶段文件清单、状态值和审批依赖的机器可读契约
```

## 安装

本仓库根目录就是 skill 目录，软链接进 agent 的 skills 目录即可：

```sh
ln -s "$PWD" ~/.claude/skills/brand-system   # Claude Code
ln -s "$PWD" ~/.codex/skills/brand-system    # Codex
```

然后在品牌工作区（另一个目录）里描述需求即可触发；Claude Code 也可以用 `/brand-system` 直接调用。

## 浏览器工作台

推荐从工作区父目录启动本地工作台，自动扫描当前目录和一级子目录；不需要手工复制 brief 模板：

```sh
python3 scripts/workbench.py . --open                         # 启动并打开浏览器
python3 scripts/workbench.py . --open --workspace brand       # 直接打开已有工作区
```

通过 skill 启用时，agent 默认就这样启动工作台并给出地址，生成、审阅和审批都在网页里完成；只有你明确要求、或环境起不了本地服务时才改在聊天里推进。

运行前提：Python 3.9+；生成需要已安装并登录的 `codex` CLI；在设置里选了 Claude Code 的生成或审查需要已安装并登录的 `claude` CLI。

打开终端输出的 `http://127.0.0.1:8765/`。工作台只监听本机，并拒绝来自其他网站的请求（校验 `Host` 与 `Origin`），所以浏览器里打开的其他页面不能替你记录审批或启动任务；预览页在沙箱里打开，生成内容里的脚本不能调用工作台接口。关闭工作台（Ctrl+C 或关闭终端）时，仍在运行的生成和审查进程会一起结束。工作台会在同一页面浏览选择生成目录、填写产品信息，并可浏览选择只读的源码/文档参考目录。默认推荐在启动目录下创建 `brand`；当前目录有内容时，工作台不会静默覆盖文件。页面会把当前工作区和阶段写入 URL，方便后退和恢复进度，并提供滚动活动日志。

## 单文件版

不能加载 skill 目录的聊天工具，用生成的单文件 prompt：

```sh
python3 scripts/build_prompt.py              # 输出 dist/brand-system-prompt.md
```

`dist/` 是生成产物，不入库，不要手改；改 `SKILL.md` 或 `references/` 后重新生成。

## 检查脚本

```sh
python3 scripts/contrast.py '#767676' '#ffffff'           # 单个配对
python3 scripts/contrast.py --matrix pairs.json           # 配对矩阵
python3 scripts/svg_lint.py <工作区>/masters/              # SVG 发布检查
python3 scripts/icon_verify.py <工作区>/dist/assets/*.ico  # 真实格式与尺寸
python3 scripts/check_workspace.py <工作区> --phase 1      # 阶段文件、状态格式与审批门
python3 scripts/check_workspace.py <工作区> --phase 5 --release   # 发布前另查 G5
```

退出码统一为 0 通过、1 有发现、2 用法或输入错误，可以直接接进工作区的 `brand:check`。

## 测试

```sh
python3 -m unittest discover -s tests
python3 scripts/smoke_test.py
```

## 试跑

把 `evals/example-brief.json` 复制为一个空目录里的 `brand.brief.json`，调用 skill 跑到 G1，再运行 `scripts/check_workspace.py <该目录> --phase 1`。脚本只核对文件与审批状态；三套方向是否真的不同、是否达到质量要求，仍要人看审阅页判断。
