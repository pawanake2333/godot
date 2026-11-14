# 定制引擎维护

基线为 Godot 4.7.2-stable，分支为 4.7.2-stable-mcp。插件唯一开发源是本仓库 addons/godot_mcp，业务工程部署副本。构建后缀通过 BUILD_NAME 配置，插件接口保持通用。

## 必要补丁

| 位置 | 维护内容 |
| --- | --- |
| EditorInterface / EditorNode | 场景创建、就绪、内存继承判断、调试服务器 URI、资源保存、未保存文档检查与原生退出的薄绑定；原生扩展脚本及帮助文档刷新完成查询；无头保存跳过图形缩略图 |
| EditorInspector / EditorUndoRedoManager | 属性事务与原生 UI 共用记录、资源引用、插件钩子、编辑完成和重启提示；非活动文档历史归属、空栈与重入保护 |
| SceneTreeDock | 复用原生结构引用更新、删除清理及节点持久分组信息 |
| ScriptEditorBase / ScriptEditor | CodeEdit 对应资源与原生断点操作的绑定 |
| EditorDebuggerSession / ScriptEditorDebugger | 暂停／继续／步进、栈变量、原生 profiler 信号与采样所有权；会话日志元数据与非活动场景路径保护 |
| EditorLog | 有界诊断环与调试来源元数据；真实 Output 控件状态、文本查询与原生 UI handler 转发 |
| EditorFileSystem | 重导入进度任务使用唯一标识 |
| GDExtension / GDExtensionManager | 已加载库的原始路径、注册类与重载能力查询；显式重载和自动重载共用原生刷新流程，完成代次覆盖延后脚本语言刷新 |
| EditorHelp | 帮助文档生成完成状态；代次过滤过期延后回调，退出时先标记清理并等待生成线程，阻止已清理文档上的延后生成 |
| macOS OS 动态库装载 | 编辑器每代使用同目录唯一临时副本，避免 dyld 复用保留的旧映像；保留原始资源路径，关闭库和编辑器结束时清理副本 |
| main / macOS、Windows DisplayServer | --hidden 离屏 GPU 绘制与可见性检查；与 headless/dummy 模式区分 |
| methods.py | BUILD_NAME 默认值，可由显式环境覆盖 |

接口说明见 [编辑器一致性约束](editor_consistency.md) 和 [Output 指南](output_control.md)。新增能力优先薄绑定既有 API／handler，不在引擎中实现另一套自动化服务器、调试协议或业务规则。

## 构建与验证

从引擎仓库根目录构建：

```bash
python3 -m SCons platform=macos target=editor arch=arm64 production=yes \
  optimize=speed_trace lto=thin debug_symbols=no no_editor_splash=yes \
  generate_bundle=yes vulkan=no -j8
codesign --verify --deep --strict bin/godot_macos_editor.app
xmllint --noout --schema doc/class.xsd doc/classes/EditorInterface.xml \
  doc/classes/EditorDebuggerSession.xml doc/classes/ScriptEditorBase.xml \
  doc/classes/GDExtension.xml doc/classes/GDExtensionManager.xml
```

原生重载专项验证会编译实际 GDExtension，通过同一常驻 MCP 会话检查新增/删除方法、
连续重载、文档与运行保护、哈希冲突和临时副本清理：

```bash
GODOT_MCP_PROCESS_TESTS=1 python3 addons/godot_mcp/tests/e2e_extension_reload_test.py \
  --godot bin/godot_macos_editor.app/Contents/MacOS/Godot \
  --interface core/extension/gdextension_interface.gen.h
```

vulkan=no 只关闭 Vulkan/MoltenVK，本构建保留 Metal。需要时通过环境配置 CLANG_MODULE_CACHE_PATH，不写入受控绝对路径。构建输出仍位于 bin/godot_macos_editor.app 和 bin/godot.macos.editor.arm64。

运行 tests/e2e_mcp_test.py 检查文档、Undo、人工操作交错、Output、运行和调试；tests/benchmark_mcp.py 用于性能测量。报告输出到临时目录或 CI 制品，不写入插件 docs。

Engine.get_version_info 的提交 hash 标识构建基底；核对工作树补丁还须检查实际 API、插件 source_sha256 和构建产物 SHA256。升级上游时逐项核对薄绑定与原生处理链，再重新构建、签名并运行对应验证。

## 部署与发布

scripts/deploy.py --manifest 从源码生成 package.json 和工具 Schema；同步时按 deployment.json 检查目标修改与删除冲突，再替换文件并核对哈希。握手验证工程、版本、协议和源码指纹。更新运行代码后重启编辑器，避免载入混合版本。

日常开发使用 bin/ 构建。正式 release/ 快照的暂存、验证和替换遵循仓库 AGENTS.md；文档或部署副本更新不表示正式快照已发布。
