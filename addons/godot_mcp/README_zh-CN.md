# Godot MCP Addon

## 作用

该 addon 将 Godot 编辑器和调试游戏能力通过 MCP tools 暴露给外部 AI 客户端。它面向本仓库构建的 `Godot_AI_Support.app`，依赖以下定制 API：

- `EditorCommandPalette.get_command_keys()`
- `EditorCommandPalette.execute_command()`
- `EditorInterface.request_game_screenshot()`
- `EditorInterface.close_scene_from_path()`
- `EditorInterface.get_editor_log()`（0.2.0 的日志工具）

## 目录

```text
addons/godot_mcp/
├── plugin.cfg
├── editor/
│   ├── godot_mcp_plugin.gd
│   └── runtime_debugger.gd
├── runtime/
│   └── runtime_helper.gd
└── sidecar/
    └── godot_mcp_server.py
```

EditorPlugin 在 `127.0.0.1` 上使用系统分配的端口监听本地 JSON-RPC，将端口、进程 ID、
工程路径和随机 Token 写入 `<工程>/.godot/mcp/endpoint.json`，权限为 `0600`。
每个工程使用独立端口；同一工程支持最多 16 个客户端连接。Python sidecar 使用标准输入输出
实现 MCP，并在连接时核对编辑器实际工程路径。Runtime Helper 作为 Autoload 仅在调试游戏中
注册 `godot_mcp` debugger namespace，发送就绪握手后才接受运行态操作。

## 安装

1. 将整个 `godot_mcp` 目录复制到目标工程的 `addons/` 下。
2. 使用 `Godot_AI_Support.app` 打开工程。
3. 在“项目 > 项目设置 > 插件”中启用 `Godot MCP`。
4. 编辑器输出出现 `GODOT_MCP: listening on 127.0.0.1:<端口>` 后配置 MCP 客户端。

MCP 客户端配置示例：

```json
{
  "mcpServers": {
    "godot": {
      "command": "python3",
      "args": [
        "/absolute/project/path/addons/godot_mcp/sidecar/godot_mcp_server.py",
        "--project",
        "/absolute/project/path"
      ]
    }
  }
}
```

sidecar 只使用 Python 3 标准库，不需要安装 pip 依赖。编辑器必须在 MCP 客户端调用工具前启动，并保持插件启用。
安装到工程内的 sidecar 也可通过父目录中的 `project.godot` 自动发现工程。
断线后的下一次调用重新发现端口并核对工程身份；失败的修改请求不会自动重放。

### Agent 会话没有注册 Godot MCP 工具时

工具注册由 MCP 客户端管理，插件不能修改当前 agent 会话的工具列表。可以通过 CLI
调用相同的 MCP tools，返回与 `tools/call` 一致的 JSON：

```bash
python3 /absolute/project/addons/godot_mcp/sidecar/godot_mcp_server.py \
  --project /absolute/project --call godot_get_state

python3 /absolute/project/addons/godot_mcp/sidecar/godot_mcp_server.py \
  --project /absolute/project --call godot_create_editor_node \
  --arguments '{"parent_path":".","name":"SpawnPoint","script_path":"res://scripts/ServerNpcSpawnPoint3D.gd","properties":{"archetype_id":17}}'

python3 /absolute/project/addons/godot_mcp/sidecar/godot_mcp_server.py \
  --project /absolute/project --call godot_capture --output-image /tmp/game.png
```

`--list-tools` 无需启动编辑器即可读取目录。`--output-image` 将截图保存到指定文件。
CLI 工具调用失败时退出码为 1；连接错误会指出工程 endpoint 路径与启动条件。

## MCP Tools

- `godot_get_state`：读取版本、工程、编辑场景和运行状态。
- `godot_get_open_scenes`：列出已打开场景、未保存场景和当前活动场景。
- `godot_close_scene`：按路径关闭场景；存在未保存修改时默认拒绝，传入 `force=true` 才丢弃修改。
- `godot_open_scene`：按路径打开场景；场景已打开时切换到对应标签。
- `godot_list_commands`：列出固定白名单中的已注册命令。
- `godot_run_command`：执行白名单命令。
- `godot_play` / `godot_stop`：启动或停止游戏。
- `godot_capture`：获取嵌入式、浮动或独立游戏窗口的 PNG。
- `godot_get_edited_scene`：读取编辑态场景树。
- `godot_inspect_editor_node`：读取编辑态节点属性。
- `godot_set_editor_property`：通过 Undo/Redo 修改编辑态属性。
- `godot_create_editor_node`：通过 Undo/Redo 创建原生或脚本节点，可设置初始属性。
- `godot_delete_editor_node`：通过 Undo/Redo 删除节点，撤销时恢复子树、顺序和 owner。
- `godot_editor_undo` / `godot_editor_redo`：操作当前编辑场景的历史。
- `godot_reload_project`：等待工程文件系统扫描完成，再通过编辑器重载已打开的脚本。
- `godot_save_scene`：保存当前场景。
- `godot_get_runtime_tree`：读取调试游戏场景树。
- `godot_inspect_runtime_object`：读取运行时对象属性。
- `godot_set_runtime_property`：修改运行时对象属性。
- `godot_send_input_action`：按下或释放 InputMap action。
- `godot_send_mouse_event`：注入原始鼠标按键、相对移动或滚轮事件，并等待一帧。
- `godot_wait_runtime_frames`：等待运行游戏的帧通知，用于输入与截图同步。
- `godot_get_logs`：按 cursor 增量读取编辑器 Output 和运行时诊断。

0.2.0 共注册 25 个 MCP tools。节点操作限定在当前场景：禁止删除根节点或修改实例/
继承场景内部节点。创建脚本节点可省略 `type`，使用脚本的原生基类。
新节点 owner 指向场景根节点，保存后成为 `.tscn` 的一部分。
保存和导入会运行嵌套编辑器循环，TCP 请求分发包含重入保护。

鼠标坐标使用游戏 viewport 像素坐标；`motion` 的 `delta_x` / `delta_y` 同时写入
`relative` 与 `screen_relative`。`button` 支持左键 1、右键 2、中键 3 和扩展键 8/9；
`wheel` 使用 `direction=up|down|left|right` 和 `factor`，自动发送释放事件。
修饰键为 `ctrl`、`shift`、`alt`、`meta`。使用按键释放结束拖动：

```json
{"kind":"button","button":2,"pressed":true,"x":100,"y":100}
{"kind":"motion","x":120,"y":95,"delta_x":20,"delta_y":-5}
{"kind":"button","button":2,"pressed":false}
{"kind":"wheel","direction":"up","factor":1}
```

外部工具直接修改 `.tscn` 前，应先调用 `godot_get_open_scenes`。如果目标场景已打开且没有
未保存修改，先调用 `godot_close_scene`，写盘后再调用 `godot_open_scene`。不要通过
`force=true` 静默丢弃用户尚未保存的编辑器修改。

Vector 和 Color 等非 JSON 类型使用带 `_type` 的对象：

```json
{"_type":"Vector3","x":1.0,"y":2.0,"z":3.0}
```

## 日志与自动化诊断

`godot_get_logs` 使用定制编辑器的 `EditorInterface.get_editor_log()`。
编辑器在插件启动前已经收集 Output 消息；运行时错误另外收集 `file`、`line`、`function`
和最多 64 层 `stack`。历史独立于 Output 面板的过滤、折叠和清空，保留最近 2048 条，
每条消息正文限制为 16384 个字符。

### 编辑器日志

为了让自动化机制能看到插件加载前发生的解析、导入和初始化错误，应通过 `--log-file` 启动
编辑器：

```bash
mkdir -p "/absolute/project/path/.godot/mcp"

"/absolute/path/Godot_AI_Support.app/Contents/MacOS/Godot" \
  --editor \
  --path "/absolute/project/path" \
  --log-file "/absolute/project/path/.godot/mcp/editor.log"
```

编辑器日志包括 GDScript 解析错误、插件加载错误、资源导入错误、`push_error()`、
`push_warning()` 和编辑器内部错误。如果 MCP 插件本身无法解析，TCP 服务不会启动，此时
需要直接读取该日志文件。

### 调试游戏日志

桌面平台默认启用运行日志，工程设置为：

```text
debug/file_logging/enable_file_logging
debug/file_logging/log_path = "user://logs/godot.log"
debug/file_logging/max_log_files
```

macOS 上默认文件位置为：

```text
~/Library/Application Support/Godot/app_userdata/<工程名>/logs/godot.log
```

### 增量日志接口

示例：

```json
{
  "name": "godot_get_logs",
  "arguments": {
    "levels": ["error", "warning"],
    "cursor": 120,
    "max_entries": 200
  }
}
```

返回值包含 `next_cursor`、`latest_cursor`、`oldest_cursor`、`dropped`、`has_more`、
`editor_pid` 和结构化 `entries`。`max_entries` 限制扫描的历史条数，`levels` 在扫描后
过滤；即使过滤后没有消息，也要推进 `next_cursor`。`dropped=true` 表示旧历史已被覆盖。
cursor 属于单个编辑器进程，进程变化后从 0 重新读取。

自动化调用按以下顺序执行：

1. 记录操作前的 `latest_cursor`。
2. 调用 Godot MCP tool。
3. 等待操作完成或达到超时。
4. 使用操作前 cursor 查询新增日志。
5. 根据 `entries[].level` 判断错误和警告，并根据来源定位问题。

`godot_reload_project` 保留编辑器处理脚本文件冲突的机制；若有未保存的脚本修改，
编辑器可能要求处理冲突。它不丢弃未保存的场景，也不重新打开已关闭的场景。

## 当前边界

- 仅监听回环地址，每个编辑器实例使用随机 Token；endpoint 文件由当前用户读取。
- 工具未注册到当前会话时使用 CLI；自动注册仍需 MCP 客户端配置和重新连接。
- Runtime Helper 只在 `EngineDebugger.is_active()` 时注册控制通道。
- 截图要求本地调试游戏，支持嵌入式、浮动和独立窗口。
- 运行态操作当前选择第一个已就绪的调试实例，不支持指定多个游戏实例。
- Runtime 请求超时为 10 秒，sidecar MCP 请求超时默认 15 秒。
- 命令执行采用固定白名单，不会动态开放未知 Command Palette 命令。

## 验证

```bash
PYTHONPYCACHEPREFIX=/tmp/godot-mcp-pycache python3 -m unittest discover \
  -s addons/godot_mcp/tests -p 'test_*.py' -v

python3 addons/godot_mcp/tests/e2e_mcp_test.py \
  --sidecar addons/godot_mcp/sidecar/godot_mcp_server.py \
  --godot bin/godot_macos_editor.app/Contents/MacOS/Godot

python3 addons/godot_mcp/tests/e2e_mcp_test.py \
  --sidecar addons/godot_mcp/sidecar/godot_mcp_server.py \
  --godot bin/godot_macos_editor.app/Contents/MacOS/Godot --require-capture
```

E2E 自动复制插件到隔离临时工程，通过文件事件等待 endpoint，测试后关闭自己的编辑器并
删除临时工程。测试包括未保存保护、节点子树撤销和持久化、日志容量及分页、原始鼠标输入、
运行时诊断、多个客户端和 CLI；图形模式还要求返回有效 PNG。同步使用 fd、进程或帧
事件，没有固定睡眠。fixture 通过 `.gdignore` 排除，不会向实际工程注册测试类。
