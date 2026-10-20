# InventorBridge 核心接口使用说明

[English](README.md)

本仓库发布桌面 InventorBridge 当前修改版的核心接口，通过本地 MCP 协议让 AI 客户端调用 Inventor 的 COM API。原核心源码逐字节保留，来源及校验值见 [SOURCE_MANIFEST.json](SOURCE_MANIFEST.json)。

发布内容仅含三个核心程序、两个部署与检查脚本、依赖清单和必要说明。用户已有的模型、装配、照片、日志、报告、虚拟环境、本机客户端配置及一次性建模脚本均未包含。

## 工作方式

AI 客户端通过本地标准输入输出连接 `server.py`；每次具体操作由独立的 `inventor_backend.py` 工作进程调用 Inventor。`quickchanger_model.py` 是装配工具依赖的概念模型模板，这三个核心文件需要一起保留。

本接口需要用户自行安装并获得授权的 Autodesk Inventor。AI 模型与账户由客户端提供，接口自身不直接调用 OpenAI 模型 API，也不包含 Autodesk 软件或许可证。

## 部署

环境要求为 Windows x64、64位 Python 3.11或更新版本，以及支持本地 stdio MCP 的客户端。依赖固定为 MCP 1.30.0、pywin32 312、Pillow 12.3.0。

把整个精简仓库放在独立目录中，保持五个 Python 文件与依赖清单位于同一层。在该目录打开 PowerShell：

```powershell
python .\setup_bridge.py --check-only
python .\setup_bridge.py --setup
```

第一条检查平台、文件完整性、配置语法和 Inventor 安装注册信息，不启动 Inventor。第二条创建或复用当前目录的 `.venv`、安装依赖、检查 MCP 初始化与工具列表，并尝试读取 Inventor 状态；随后生成当前路径对应的项目级 `.codex/config.toml`，保留其他已有配置。它不修改全局客户端配置，不安装 Inventor，也不创建 CAD 模型。

不同客户端若采用其他 MCP 配置格式，可将命令设为本目录 `.venv\Scripts\python.exe`，参数设为 `server.py` 的绝对路径，工作目录设为本目录；概念装配工具建议留出300秒工具调用时间。项目可信状态和注册方式按所用客户端说明处理。

安装后也可以单独检查：

```powershell
.\.venv\Scripts\python.exe -B .\verify_mcp.py
```

这个检查脚本会初始化 MCP、列出12个工具，并调用只读的 `inventor_status`。接口就绪与 CAD 会话可用分别报告。实际建模时，Inventor 与接口需要处于同一 Windows 用户会话和兼容的权限级别。

## 支持哪些操作

- 状态与启动：读取运行状态；连接或启动已安装的 Inventor。
- 原生板件：创建带中心通孔的矩形板，打开、检查、修改参数后另存，返回模型视口预览。
- 概念装配：创建固定参数化快换装配，打开、检查、预览，并显示已生成的分离状态。

完整的12个工具名称见英文首页。可以先向已连接接口的 AI 客户端提出一个具体任务：

> 创建名为 demo_plate_01 的板件，长100毫米、宽60毫米、厚10毫米，中心通孔直径20毫米。完成后核对包围盒、体积与命名尺寸，并返回预览图。

每次创建或另存使用新名称。接口拒绝覆盖已有文件；板件更新只接受本接口生成且没有未保存修改的模型。输入尺寸使用毫米，程序在调用 Inventor 时转换单位。

模型输出限制在项目 `models` 目录；预览与操作记录分别写入 `previews`、`logs`。依赖安装和 COM 包装器可能使用其标准缓存位置。

## 能力与验证范围

当前创建工具覆盖板件和固定结构的参数化概念快换装配。其他零件需要继续扩展建模逻辑。参考图片参数用于校验图片路径和记录来源，接口不会直接从任意图片的像素恢复三维结构。

快换装配默认尺寸属于概念假设；上下分离状态为静态示意。制造公差、承载能力、磁路和解锁运动仍需独立验证。

常规工作进程限时45秒，概念装配创建限时240秒。超时或取消后，Inventor 可能已经完成部分操作，应先检查软件、输出和日志，再决定下一步，不能自动重试创建。

本次发布只验证源码、依赖、MCP 初始化和12个工具的发现，未调用 CAD 工具、连接 Inventor 或生成模型。详见 [VERIFICATION.md](VERIFICATION.md)。历史本机建模结果不能当作另一台电脑部署成功的证明。

## 与机械臂项目的关系

[SinoGermanRobot](https://github.com/Learn-to-innovate-and-Surpass/SinoGermanRobot) 提供网页仿真、动作编排与文件审核。InventorBridge 是独立 CAD 工具，不接入该项目网页仿真服务或机器人控制工作进程。

自编接口代码及说明采用 [MIT许可证](LICENSE)。Autodesk 软件、商标、官方资料、第三方依赖以及使用者自己的模型和图片遵循各自授权。
