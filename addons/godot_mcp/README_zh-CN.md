# Godot MCP 使用指南

**0.6.1 / 协议 2，默认 24 个工具，完整注册能力 62 项。** 适配本仓库 Godot 4.7.2 定制编辑器，插件面向通用开发流程。

日常脚本、已保存场景、资源和配置优先直接修改文件。MCP 负责编辑器状态协调、运行验证、输入、画面和调试；需要操作未保存文档、只存在于内存的资源或保留 Undo 时，再按需使用扩展编辑工具。

逐项用途、参数、返回结果、边界和 62 个示例见 [工具参考手册](docs/tool_reference_zh-CN.md)。[工具契约](docs/tool_catalog.json) 是完整注册表；[文档索引](docs/README.md) 汇总当前说明与使用约束。

## 日常工作流

1. editor_status/scene_list 确认工程与未保存内容。目标文档有未保存修改时先处理冲突，不能直接覆盖。
2. 用代码和文件工具修改磁盘内容。已打开场景需要关闭或重开时，按需调用对应文档工具；不通过多次节点 RPC 重写一份已经保存的场景。
3. 外部文件变更后按需 filesystem_scan。资源重导入、脚本重载和游戏重启分别处理，不在每次改动后全部执行。
4. run_start → job_wait → 从成功结果取 run_id/session_id → 检查对象、输入、等待条件、截图或调试 → run_stop。

本文流程中的短名称均省略 godot_ 前缀；实际调用使用完整名称。

## 默认目录与按需能力

默认 tools/list 只返回 24 项，减少初始 Schema 和选工具成本。其余 38 项可按领域发现；所有能力仍由同一个注册表校验和分发。

```json
{"query":"godot_edit_batch","tier":"extended","include_schema":true,"limit":1}
```

以上参数传给 godot_tools_discover。取得契约后，通过 godot_tools_call 调用需要的扩展能力：

```json
{
  "tool_name":"godot_edit_batch",
  "arguments":{
    "scene_id":"<scene_id>",
    "expected_revision":7,
    "operations":[
      {"op":"create","id":"probe","type":"Node3D","name":"Probe"},
      {"op":"set","node_path":"$probe","changes":{"position":{"_type":"Vector3","x":1,"y":2,"z":3}}}
    ]
  }
}
```

该例用于当前内存场景和 Undo 需求，标识与版本需替换为实际返回值。普通已保存文件编辑继续使用文件工具。

发现入口默认每页 20 个摘要，不返回 Schema；用 query/domains/tier 缩小范围，再显式请求 include_schema=true。元数据 godot/tier 区分 core/extended。需要全部工具一次注册时，以 --toolset all 启动 sidecar；默认 --toolset core。

节点编辑统一使用 edit_batch 的 create/delete/rename/reparent/duplicate/attach/groups/connect/disconnect。批量预检与一次 Undo 保留，atomic=false；项目 setter、脚本和外部副作用不能保证回滚。改名或移动复用原生 NodePath/动画轨迹引用更新，遵守编辑器偏好。

## 配置与启动

工程中安装本插件并在编辑器插件设置中启用。PROJECT、GODOT、PLUGIN 来自显式环境或使用者的本地配置，不要将本机路径写入受控文件。

```bash
python3 addons/godot_mcp/scripts/deploy.py --manifest
python3 addons/godot_mcp/scripts/deploy.py --target "$PROJECT/addons/godot_mcp"
"$GODOT" --editor --path "$PROJECT"
```

部署固定排除 `tests/`（包含测试脚本、fixtures 及其生成缓存）；测试只保留在 Godot 源仓库。旧副本中的测试文件会先与已有部署清单比对，确认未修改后移除；未知文件、本地修改或符号链接会阻止迁移。

同步器基于 deployment.json 对目标进行冲突检查，再原子替换文件并验证哈希；本地修改、删除或未知文件会拒绝覆盖。无清单的旧副本须审阅后用 --baseline 首次接管。UID 和缓存不作为手改源码覆盖。生产插件更新后重新启动编辑器，避免载入不同包。

MCP 客户端配置示例中的占位符需由客户端展开：

```json
{
  "mcpServers":{
    "godot":{
      "command":"python3",
      "args":["${PLUGIN}/sidecar/godot_mcp_server.py","--project","${PROJECT}","--godot","${GODOT}"]
    }
  }
}
```

sidecar 无编辑器时也可响应初始化、目录发现和任务查询。editor_start 复用/启动指定工程，等初始扫描和文档恢复；--auto-start 可在后台任务中自动复用或启动正常显示的编辑器。editor_status 在失败时给出 reason/startup_job，随后 job_wait/diagnostics_read/artifact_read 可检查启动结果。

endpoint 位于工程 .godot/mcp/endpoint.json，使用随机回环端口和 Token，文件权限 0600。握手核对工程、版本、协议与源码哈希；不复制 Token，不固定端口。同一工程只启动一个插件编辑器，可接入多个客户端。

**跨步骤操作保持一个常驻 sidecar。** 任务、产物、输入所有权和自有游戏属于服务。服务退出清理自有游戏及临时产物，编辑器继续保留；逐条 CLI 不适合跨调用管理 job/run。

```bash
# 一次性读取
python3 "$PLUGIN/sidecar/godot_mcp_server.py" --project "$PROJECT" --call godot_editor_status
# 默认和完整目录
python3 "$PLUGIN/sidecar/godot_mcp_server.py" --list-tools
python3 "$PLUGIN/sidecar/godot_mcp_server.py" --list-tools --toolset all
```

## 目标与安全边界

- 编辑器修改使用明确 scene_id 和最新 expected_revision；整个编辑器关闭/重启使用 editor_epoch 与 expected_document_revision。未保存内容默认拒绝丢弃。
- 运行操作明确 session_id，不自动选择第一个游戏。只有当前服务自有 run_id 可以停止；外部已连接会话可检查和输入。
- headless_logic 是 dummy 逻辑模式；hidden_gpu 隐藏主窗口并保留 GPU 绘制，需要可用图形登录会话；visible 正常显示。macOS 已验证 Metal。dummy 不伪造 first_render 或截图。
- 输入持有归连接所有，释放和断连清理不影响另一连接持有的输入；序列完成或中断会释放。runtime_wait 等实际帧、信号或条件，不使用硬睡眠。
- script_read/patch 仅处理 CodeEdit 内存缓冲区，保留历史且不直接写盘；普通代码编辑使用文件工具。script_reload 拒绝打开的文档，不承诺运行对象热重建。
- 资源编辑属于扩展能力。共享 Resource 改动影响使用者，外部资源单独保存；覆盖要求当前磁盘哈希。继承/实例结构受保护，导入资源先复制。
- 动画 tracks 返回插值；isolated 采单 clip、无项目脚本且在 modifiers 前；live 只读已应用姿态，不 seek。不是确定性物理仿真。
- 暂停栈变量及单步走现有调试通道，已验证 GDScript；profiler 按需开启，受帧数和期限约束。其他语言与线程能力依赖 Godot。

成功数据在 structuredContent，身份在 _meta.godot。超时、断线或结果契约失败不等于撤销；先读状态核对，不自动重放修改。对象引用、快照、JSON、任务、事件和产物均有预算与有效期，详见手册。

## Output 面板

默认工具 godot_output_control 提供 get/configure/read/clear：查询状态和计数，显示或收起、级别/搜索/折叠，读取纯文本，以及明确范围清空。详见 [Output 指南](docs/output_control.md)。

clear 默认只清面板文字、计数和图标，保留 MCP 诊断。清诊断必须给 editor_epoch、scope=diagnostics|all 和 through_cursor，保留截止值之后的新日志；游标从不归零，history_generation 与 dropped 可识别历史变化。Debugger 独立错误树和原始启动日志文件不随 Output 清空。

## 原生无头流程

测试、业务烘焙、导出、静态验证/依赖、磁盘搜索和图像比较不进入 MCP。使用 Godot 原生 CLI、工程工具链与外部图像工具；MCP 只在需要会话状态时参与检查。详见 [职责说明](docs/tool_scope.md)。

## 验证与维护

```bash
GODOT_MCP_PROCESS_TESTS=1 python3 -m unittest discover -s addons/godot_mcp/tests -p 'test_*.py'
python3 addons/godot_mcp/tests/e2e_mcp_test.py --sidecar addons/godot_mcp/sidecar/godot_mcp_server.py --godot "$GODOT"
```

E2E 默认隐藏窗口；用户明确要求正常显示时才添加 --visible。测试按精确临时工程和可执行文件回收进程。运行结果与性能报告输出到系统临时目录或 CI 制品，不写入插件 docs。

原生补丁、构建与升级方式见 [引擎维护](docs/engine_patches.md)；属性命令、Undo、资源引用和人工操作共存规则见 [编辑器一致性约束](docs/editor_consistency.md)。
