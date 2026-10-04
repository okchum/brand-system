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
python3 scripts/workbench.py .
```

打开终端输出的 `http://127.0.0.1:8765/`，在页面中选择已有工作区，或创建新的 `brand-workspace`。当前目录有内容时，工作台不会静默覆盖文件；空目录可以直接初始化为工作区。工作台会创建 brief、项目状态、范围、策略和交接文件，并提供阶段检查入口。

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
