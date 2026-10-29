# 论文审阅工作台（ThesisReviewWorkbench）

面向高校"虚拟助教"场景的论文批改辅助系统：从 SVN 工作副本取稿，解析 DOCX / PDF，按教师规则库做结构、格式与内容检查，生成可复核的审阅报告，并提供浏览器端与 Electron 桌面客户端两种用法。

> 本仓库只包含源码与规则库。学生论文原文、历史批注、模型 API Key 均不随仓库分发。

## 主要能力

- **取稿**：按 SVN 版本/目录采集学生论文，保存快照与来源凭据，支持初检与重传复核。
- **解析**：DOCX 评论读取、正文/表格/分页结构提取、PDF 文本与渲染图审阅。
- **规则检查**：教师规则库（格式、标题层级、图表题注、参考文献、摘要页等）与跨学生规则治理。
- **内容审阅**：证据驱动的上下文审阅、模型辅助语义检查、隐私边界与审阅契约校验。
- **报告与交付**：Markdown/JSON 报告、任务完成度跟踪、交付包生成。
- **界面**：本地 Web 后端（带随机会话密钥）+ React/Vite 前端；Electron 桌面版含隔离 Python 运行时，开箱即用。

## 目录结构

| 路径 | 说明 |
| --- | --- |
| `*.py`（根目录） | 取稿、解析、规则、审阅流水线与测试模块 |
| `web_backend/` | 本地 HTTP 服务、证据适配、新论文审阅服务 |
| `web_frontend/` | React + TypeScript + Vite 前端 |
| `desktop-app/` | Electron 桌面客户端（打包脚本与内置引擎装配） |
| `desktop-client/` | 桌面版构建产物（不入库） |
| `nanobot/` | 内置（vendored）框架源码，由 `bootstrap.ps1` 绑定为固定工具集 |
| `deliverables/` | 阶段验收记录与实验报告 |
| `data/`、`output/` | 运行数据与构建输出（不入库） |

## 快速开始

```powershell
# 1. 创建虚拟环境并安装依赖（Python 3.13 会使用锁定文件）
./bootstrap.ps1 -Python python -Environment .venv

# 2. 配置 SVN 工作副本（可选；不写则用默认值）
#    config.json: {"workcopy": "D:/SVN虚拟助教", "svn_executable": "C:/Program Files/TortoiseSVN/bin/svn.exe"}

# 3. 启动本地 Web 服务与界面
./.venv/Scripts/python.exe run_agent_ui.py --port 8787
```

前端开发：

```powershell
cd web_frontend
npm ci
npm run dev      # 开发服务器
npm run build    # 产出 dist/
```

桌面客户端：

```powershell
./.venv/Scripts/python.exe -B build_standalone.py   # 生成 output/standalone_*/engine
cd desktop-app
npm ci
npm run dist
```

## 测试

```powershell
./.venv/Scripts/python.exe -m pytest -q          # 根目录测试
./.venv/Scripts/python.exe -m pytest web_backend -q
cd desktop-app; npm test                          # Electron 主进程测试
```

## 技术栈

Python 3.13 · python-docx · lxml · PyMuPDF · Pillow · openai SDK · React 18 · TypeScript · Vite · Tailwind CSS · Electron 44 · electron-builder

## 隐私与安全

- `data/`、`output/`、`config.json`、`.env*` 已在 `.gitignore` 中排除；请不要把它们提交进仓库。
- 模型审阅需要用户自行提供 Key，Key 不落盘进仓库；服务端用随机会话密钥校验本地界面请求。
- 规则内置，但**不代替**学校/学院规范的最终确认。

## 许可

未声明开源许可；如需对外发布请先补充 LICENSE。
