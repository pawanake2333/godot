# 本地开发说明

当前仓库是 Godot 4.7.2 的定制分支，增加了供 MCP 自动化调用的编辑器 API。
下述修改尚未提交。除非用户明确要求，不要改变现有文件的暂存/未暂存状态。

## 仓库关系

当前仓库 `godot_ai_support_4_7_2` 同时包含 Godot 引擎定制 API 和一份完整的
MCP 插件副本：

- `addons/godot_mcp/`：当前仓库内可直接复制到目标 Godot 工程的插件。

该副本从以下同级工作树复制而来：

- `../godot_add_ai_support/addons/godot_mcp/`：本次复制时的来源目录。
- `../godot_mcp_test_project`：安装了该插件的 MCP 端到端测试工程。

MCP 插件主要由以下部分组成：

- `addons/godot_mcp/editor/godot_mcp_plugin.gd`：编辑器内的 TCP JSON-RPC 服务。
- `addons/godot_mcp/editor/runtime_debugger.gd`：编辑器与调试游戏之间的请求转发。
- `addons/godot_mcp/runtime/runtime_helper.gd`：运行时场景树、属性和输入操作。
- `addons/godot_mcp/sidecar/godot_mcp_server.py`：通过标准输入输出提供 MCP 协议的
  Python sidecar。
- `addons/godot_mcp/tests/e2e_mcp_test.py`：现有 MCP 端到端测试。

当前副本最初从 `../godot_add_ai_support/addons/godot_mcp/` 原样复制，随后已经在当前
仓库中加入场景文件协调工具和独立窗口截图支持说明，因此两份代码现在存在差异。两者不是
符号链接或 Git 子模块，后续修改不会自动同步。当前仓库内的副本应视为配合本仓库定制引擎
使用的版本；需要时再明确地将修改回写来源工作树。

## 本地修改

### 命令面板自动化

`EditorCommandPalette` 新增公开接口：

- `get_command_keys() -> PackedStringArray`：返回排序后的已注册命令键。
- `execute_command(key_name)`：按命令键执行已注册命令。

相关文件：

- `editor/settings/editor_command_palette.{h,cpp}`
- `doc/classes/EditorCommandPalette.xml`

### 运行游戏截图

`EditorInterface.request_game_screenshot(callback) -> bool` 通过活动的游戏调试会话请求
截图。回调签名如下：

```gdscript
func callback(width: int, height: int, temporary_png_path: String) -> void:
    pass
```

该接口支持嵌入式、编辑器浮动窗口和独立游戏窗口。没有连接到调试器的运行项目时返回
`false`。回调是异步的，调用方读取完成后应删除临时 PNG。

实现约束：

- `GameView::_instance_rq_screenshot()` 使用空 `Rect2i` 表示非嵌入式游戏截图。
- `EditorNode::_save_screenshot_with_embedded_process()` 检测该标记后直接保存游戏 PNG，
  不再与编辑器视口截图混合。
- 不要重新加入旧的 `window_wrapper->get_window_enabled()` 拒绝逻辑，否则浮动窗口会
  再次无法截图。
- 多个活动调试会话可能触发多次回调；当前 MCP 使用场景默认只有一个运行实例。

相关文件：

- `editor/editor_interface.{h,cpp}`
- `editor/run/game_view_plugin.cpp`
- `editor/editor_node.cpp`
- `doc/classes/EditorInterface.xml`

### 场景文件协调

已有编辑器 API：

- `EditorInterface.get_open_scenes()`
- `EditorInterface.get_unsaved_scenes()`
- `EditorInterface.open_scene_from_path(path)`

场景已经打开时，`open_scene_from_path()` 会切换到对应标签。本地修改新增：

- `EditorInterface.close_scene_from_path(path) -> Error`

关闭操作按路径直接移除对应场景，不会先切换到该标签。返回值可能是 `OK`、
`ERR_DOES_NOT_EXIST` 或 `ERR_BUSY`。

该接口与 `EditorInterface.close_scene()` 一样会丢弃场景中尚未保存的编辑器修改。
MCP 直接修改 `.tscn` 前应遵循以下流程：

1. 调用 `get_open_scenes()` 和 `get_unsaved_scenes()`。
2. 未经用户明确确认，不要覆盖出现在未保存列表中的场景。
3. 写文件前调用 `close_scene_from_path(path)`。
4. 修改磁盘上的 `.tscn`。
5. 如果场景原本打开，调用 `open_scene_from_path(path)` 重新打开。

相关文件：

- `editor/editor_interface.{h,cpp}`
- `editor/editor_node.{h,cpp}`
- `doc/classes/EditorInterface.xml`

### MCP 插件

当前仓库的 `addons/godot_mcp/` 已新增以下 MCP tools：

- `godot_get_open_scenes`：返回已打开场景、未保存场景和当前活动场景。
- `godot_close_scene`：按路径关闭场景；默认拒绝关闭未保存场景，只有显式传入
  `force=true` 才会丢弃修改。
- `godot_open_scene`：按路径打开场景；已经打开时切换到对应标签。

`godot_get_state` 现在也包含 `open_scenes` 和 `unsaved_scenes`。`godot_capture` 的工具
说明和错误信息已经更新，不再限定为嵌入式 Game View。

相关文件：

- `addons/godot_mcp/editor/godot_mcp_plugin.gd`
- `addons/godot_mcp/sidecar/godot_mcp_server.py`
- `addons/godot_mcp/tests/e2e_mcp_test.py`
- `addons/godot_mcp/README_zh-CN.md`

## macOS 编译

使用以下本地构建配置：

```bash
python3 -m SCons platform=macos target=editor arch=arm64 \
  production=yes optimize=speed_trace lto=thin debug_symbols=no \
  no_editor_splash=yes generate_bundle=yes vulkan=no -j8
```

环境注意事项：

- 当前 `PATH` 中没有可直接执行的 `scons`，使用 `python3 -m SCons`。
- 2026-10-14 当前默认 `python3` 是缺少 SCons 的 Conda Python；已有 SCons 安装在
  `/usr/bin/python3` 的环境中，实际构建使用 `/usr/bin/python3 -m SCons`。
- 未安装并配置 MoltenVK SDK 时必须保留 `vulkan=no`。否则
  `platform/macos/detect.py` 会在配置阶段调用 `sys.exit(255)`；在非 TTY 或缓冲输出
  环境中可能只看到无日志的退出码 `255`。
- `vulkan=no` 不会禁用 Metal。当前构建默认使用 macOS Metal 渲染驱动。
  Compatibility 渲染器日志可能显示 `OpenGL API 4.1 Metal`。
- AccessKit 和 ANGLE 依赖缺失提示在当前配置下只是警告，不影响构建。

构建产物：

- `bin/godot.macos.editor.arm64`
- `bin/godot_macos_editor.app`

## Release 发布目录

`release/` 是已经完成修改、编译和运行时验证的最终交付快照，不是日常开发目录。
该目录会被仓库现有的 `[Rr]elease/` 忽略规则排除，不会出现在 `git status` 中；检查发布
状态时必须直接查看和验证目录内容，不能仅依赖 Git 状态。

当前结构：

```text
release/
├── Godot_AI_Support.app
├── godot_mcp/
└── EDITOR_AGENT_GUIDE.md
```

- `release/Godot_AI_Support.app`：最终 macOS arm64 编辑器。
- `release/godot_mcp/`：与该编辑器匹配的最终 MCP 插件。
- `release/EDITOR_AGENT_GUIDE.md`：面向使用 Editor 的 agent 的安装、调用、安全和
  错误处理说明。

强制规则：

- 日常开发只修改引擎源码和仓库根目录下的 `addons/godot_mcp/`，不得直接编辑
  `release/` 中的文件。
- 中间编译、临时测试、调试日志和未验证插件不得写入 `release/`。
- 不要为了让发布 App 出现在 `git status` 中而修改 `.gitignore`；当前 App 约 164 MB，
  如需版本化发布物，应使用专门的制品存储或 Git LFS，而不是普通 Git blob。
- 只有用户确认功能修改完成，并且下面列出的发布验证全部通过后，才能更新
  `release/`。
- 发布时先生成独立的 `.release_staging/`，完成校验后再整体替换 `release/`；不要逐个
  覆盖正式发布目录中的文件。
- 如果任一构建或测试失败，保留现有 `release/` 不变并删除失败的暂存目录。

发布流程：

1. 使用上面的固定 SCons 参数完成编辑器构建。
2. 对 `bin/godot_macos_editor.app` 执行
   `codesign --verify --deep --strict --verbose=2`。
3. 使用仓库根目录的 `addons/godot_mcp/` 完成 Python 语法检查和真实 MCP E2E 测试。
4. 创建全新的 `.release_staging/`。
5. 使用 `ditto` 将 App 复制为
   `.release_staging/Godot_AI_Support.app`，以保留 bundle 元数据和签名。
6. 将 `addons/godot_mcp/` 复制到 `.release_staging/godot_mcp/`。
7. 更新暂存区内面向 agent 的使用说明。
8. 再次校验暂存 App 的签名、版本、插件文件一致性和发布目录清单。
9. 所有检查通过后才用暂存目录整体替换 `release/`。
10. 使用 `release/` 中的 App 和插件再执行一次真实 MCP E2E，确认复制和重命名没有破坏
    发布物。

当前 `release/` 是 2026-09-12 验证通过的快照。后续开发即使改变了 `bin/` 或
`addons/godot_mcp/`，也不能据此自动更新该快照。

## 验证记录

以下静态检查已经通过：

```bash
xcrun clang-format --dry-run --Werror \
  editor/editor_interface.cpp editor/editor_interface.h \
  editor/editor_node.cpp editor/editor_node.h \
  editor/run/game_view_plugin.cpp \
  editor/settings/editor_command_palette.cpp \
  editor/settings/editor_command_palette.h

xmllint --noout --schema doc/class.xsd \
  doc/classes/EditorInterface.xml \
  doc/classes/EditorCommandPalette.xml

git diff HEAD --check
```

完整 macOS 编辑器构建和后续无改动增量构建均已通过。

还使用隔离的临时项目进行了编辑器端到端验证，并明确设置：

```ini
[game_view]
embed_on_play=false
make_floating_on_play=false
```

运行结果：

- 成功枚举两个 `.tscn` 场景。
- 成功按路径关闭非当前场景，当前活动场景未发生切换。
- 成功重新打开场景并切换到该场景。
- 成功启动独立游戏窗口。
- `request_game_screenshot()` 接受请求并执行异步回调。
- 返回的 PNG 可以正常加载，尺寸为 `320x180`。
- 图片中心像素符合测试场景预期的绿色。
- 编辑器日志中没有错误、重载提示或脚本解析失败。

验证结束后临时测试项目已删除。

MCP 插件还通过了真实 sidecar 端到端测试：

- `tools/list` 返回 17 个工具，包含三个新增场景工具。
- `godot_get_open_scenes` 正确返回 `res://main.tscn`。
- `godot_close_scene` 和 `godot_open_scene` 完成关闭/重新打开流程。
- 场景通过 Undo/Redo 修改后，未带 `force=true` 的关闭请求被正确拒绝。
- 使用 `embed_on_play=false` 的独立游戏窗口成功截图，sidecar 返回有效 PNG。
- 运行时场景树、属性读写和输入操作的原有测试继续通过。
- 从全新编辑器进程重跑时状态为 `passed`，Godot 日志无错误。

## 后续工作

### 2026-10-14 能力补齐

- 插件和 sidecar 开发版升级为 0.2.0，共 25 个 MCP tools。使用工程内
  `.godot/mcp/endpoint.json` 发现动态端口和 Token，支持多个客户端并核对工程身份。
- sidecar 增加 `--project`、`--call`、`--arguments`、`--output-image` 和
  `--list-tools`。会话没有注册工具时通过 CLI 调用同一 MCP 桥接接口。
- 新增创建/删除编辑器节点和场景 Undo/Redo，支持脚本节点初始属性与 owner 持久化。
- 新增原始鼠标按钮、相对位移、滚轮和修饰键注入，以及运行时帧事件等待。
- Runtime Helper 向编辑器发送 ready 握手；请求分发禁止保存/导入进度窗口触发的重入。
  第一方插件和测试不使用固定睡眠。
- `EditorInterface.get_editor_log(cursor, max_entries)` 暴露有界 Output 历史；
  `ScriptEditorDebugger` 同时记录结构化运行时错误来源和调用栈。消息不受 Output 面板的
  清空、过滤和折叠影响。headless `EditorInterface.save_scene()` 跳过图形缩略图生成。
- `godot_reload_project` 在扫描前订阅完成事件，再调用编辑器脚本重载；保留脚本冲突机制。
- 永久测试位于 `addons/godot_mcp/tests/`，fixture 有 `.gdignore`，E2E 自动创建和清理
  隔离工程。使用命令与工具参数见 `addons/godot_mcp/README_zh-CN.md`。
- 正式 `release/` 和目标业务工程的插件仍为上次发布版本；新的开发构建与隔离测试通过后，
  按本文件发布规则准备 `.release_staging/`，用户确认后再整体更新正式快照并同步目标工程。
- 当前 `.release_staging/` 已准备完毕，包含签名 App、同源码一致的插件、agent 指南和
  `manifest.json`。开发 App 的 headless/图形 E2E 同时运行通过；暂存区重命名 App 与
  暂存插件的图形 E2E 也通过。6 项 Python 单元测试、C++ 格式、XML schema 和 diff
  检查通过。E2E 中警告/错误来自有意注入的诊断 fixture，没有意外引擎错误或解析错误。

- 当前 `addons/godot_mcp/` 已包含场景协调和独立窗口截图支持；同级来源工作树
  `../godot_add_ai_support/addons/godot_mcp/` 尚未同步这些后续修改。
- 合入或发布前检查新增绑定方法对扩展 API 兼容性的影响，并按发布流程重新生成所需的 API
  快照。
- 如果该分支建立稳定的编辑器测试框架，应补充永久的自动化集成测试；本轮运行时测试使用
  的临时项目已经清理。
- 不要删除或覆盖无关本地修改。记录本文时，命令面板及初始截图 API 修改已暂存，后续截图
  与场景管理修改未暂存。
