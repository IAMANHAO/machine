# 打包与部署

## 两个产物

| 产物 | 大小 | 需要 | 说明 |
|---|---|---|---|
| `dist/mds-server/` | 46 MB | 仅 Python | 独立版，双击 `mds-server.exe` 自动开浏览器 |
| `…/bundle/nsis/*.exe` | 21 MB | + Rust | NSIS 安装包，Tauri 原生窗口 |

```powershell
./packaging/build.ps1              # 全量
./packaging/build.ps1 -SkipTauri   # 只出独立版，不需要 Rust
```

脚本会依次：构建前端 → PyInstaller 打包 → 冒烟测试 → 复制 sidecar → Tauri 构建。

## 打包态与源码态的差异

由 `server/config.py` 统一收口，三处差异都是有意的：

| | 源码态 | 打包态 |
|---|---|---|
| 引擎数据 | skill 目录 | 随包 `_internal/engine`（**只读**） |
| 用户数据 | `.mds-data/` | `%LOCALAPPDATA%\MDS\` |
| 前端 | `web/dist` | 随包 `_internal/web` |

**用户数据必须写到用户目录**：装在 Program Files 里的程序写不了自己旁边，
写在那会让项目保存静默失败。

**知识库只读**：随包数据不可改，写入接口返回 409 并说明原因。
要在打包版上改数据，设 `MDS_SKILL_ROOT` 指一份可写副本。

环境变量覆盖：`MDS_SKILL_ROOT` / `MDS_DATA_DIR`。

## PyInstaller 规格要点

`packaging/mds-server.spec`。用 onedir 不用 onefile——
onefile 每次启动都要解包几十 MB 到临时目录，冷启动慢好几秒。

### hiddenimports 里为什么有那些东西

PyInstaller 的静态分析扫不到运行时按字符串/路径加载的模块：

- `mds.*` —— 引擎住在 skill 目录，不在常规包路径上
- `procure_link` / `_yaml` / `yaml_mini` —— 采购关键词模块按路径 import
- `uvicorn.*` —— 实现类按字符串加载
- `keyring.backends.*` —— 后端运行时发现；漏了就存不了凭据

### excludes 要保守

**`PIL` 不能排。** reportlab 的 `lib/utils.py` 硬 import 它，
排掉的话 PDF 导出会在打包版上直接 `ModuleNotFoundError`——而源码态完全测不出来。

这是实际踩过的坑。为省十几 MB 体积去裁剪依赖，代价是一个只在发版后才暴露的故障。

## 打包产物必须单独冒烟测试

```bash
.venv/Scripts/python.exe packaging/smoke_test.py
```

30 项断言，起真正的 exe 走真正的流程。

**这一层不能省。** 源码态测试全绿，但打包版一开始根本跑不起来，
实际踩到三个只在打包形态暴露的问题：

1. `mds.knowledge` 用 `__file__` 推数据根 → 打包后指进归档，工作流数 0
2. `mds.procure` 运行时按路径 import `scripts/procure_link.py` → 包里没有；
   而且它自己也用 `__file__` 猜配置路径
3. spec 里把 `PIL` 排进 excludes → PDF 导出 500

前两个的根因是同一类：**`__file__` 在归档里推不出数据路径**。
解法是 `mds.knowledge.SKILL_ROOT` 优先读 `MDS_SKILL_ROOT` 环境变量，
由 `config.ensure_engine_importable()` 在导入前回写。

## Tauri 壳

`desktop/src-tauri/`。壳只做三件事：

1. 拉起随包的 sidecar：`mds-server.exe --port 0 --no-open --parent-pid <自己的PID>`
2. 读它 stdout 上的 `MDS_READY <url>`，拿到实际端口
3. 开窗口指到那个地址

端口由 sidecar 自己挑（`--port 0`）：装了两份、或本机端口被占用时不会起不来。
不用固定 sleep 等启动——冷启动时间差别很大，sleep 短了连不上、长了平白拖慢。

### sidecar 回收：两道保险

**只做一道会留下孤儿进程。** 实测确认过。

1. Tauri 的 `RunEvent::Exit` 里主动 `kill()` —— 覆盖正常退出
2. sidecar 自己盯 `--parent-pid`，父进程消失就退出 —— 覆盖强杀

第二道是必需的：任务管理器结束进程 / `taskkill /F` 时，
Rust 的 `Drop` 根本不会执行，光靠第一道会留下 `mds-server.exe`
继续占着端口。看门狗轮询间隔 2 秒。

### frontendDist 是个占位

Tauri 的 `frontendDist` 是必填项，但窗口加载的是外部 URL，它实际不被使用。
`desktop/placeholder/` 就是为此存在的，里面写明了原因。

**注意路径**：`frontendDist` 相对的是 Tauri 项目根（`desktop/`），
不是 `src-tauri/`。

## 工具链要求

| | 版本 | 备注 |
|---|---|---|
| Python | 3.10+ | |
| Node | 18+ | |
| Rust | 1.77+ | 仅 Tauri 需要 |
| MSVC | VS 2022 Build Tools | Rust 链接用 |
| WebView2 | | Win11 自带 |

本机 Rust 装在 `%USERPROFILE%\.cargo`，安装时用了 `--no-modify-path`，
**全局 PATH 没被改**。`build.ps1` 会自己找；手动用 cargo 要先：

```powershell
$env:PATH += ";$env:USERPROFILE\.cargo\bin"
```

## 发版清单

1. `cd web && npm run build`
2. `./packaging/build.ps1`
3. 冒烟测试通过（脚本会自动跑）
4. 手动验证一次装完的应用：开窗口、跑一次选型、导出一次报告、关窗后确认无残留进程
5. 检查 `%LOCALAPPDATA%\MDS\` 是否正常创建

第 4 步的最后一项容易忘，但孤儿进程是用户最容易察觉的故障。
