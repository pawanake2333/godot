# Godot MCP 工具参考手册

| 文档属性 | 内容 |
| --- | --- |
| 文档版本 | 1.4 |
| 适用插件 | Godot MCP 0.6.2 |
| 自动化协议 | 2 |
| 引擎基线 | Godot 4.7.2 定制编辑器 |
| 工具数量 | 63 个公开工具 |
| 更新日期 | 2026年11月12日 |
| 维护位置 | 定制 Godot 仓库的 addons/godot_mcp |

## 1 目的与适用范围

Godot MCP 为开发者和自动化客户端提供当前编辑器、场景文档、内存资源与调试游戏实例的通用操作接口。日常脚本、已保存场景、资源和配置优先直接修改文件。MCP 负责文件操作无法直接获得的会话状态、运行观测和调试控制；需要操作未保存文档或保留 Undo 时，才使用内存编辑能力。它保留 Godot 文档身份、Undo 历史、未保存内容、对象引用及调试会话，使自动化能够在明确目标和版本约束下完成开发任务。

本手册逐项定义当前 63 个注册工具（默认展示 25 个，扩展工具 38 个）的用途、执行位置、主要参数、核心结果、限制和调用示例。适用于日常编辑、界面验证、运行检查、动画检查、断点调试及自动化流程编写，面向通用开发流程。

烘焙、无头测试、导出、磁盘源码编辑与搜索、静态依赖扫描和图像比较由原生命令或外部工具完成。它们不在公开 MCP 目录内。旧版 25 个入口已删除，不提供兼容别名。

安装、引擎路径配置与常驻 sidecar 启动方式见 [使用指南](../README_zh-CN.md)。本手册中的 JSON 是 tools/call 的 arguments；形如 `<scene_id>` 的字符串是占位标识，必须替换为前序工具返回的真实值。例子用于说明单项契约，不表示可以按文档顺序无条件执行。正文流程中的短名称均省略 godot_ 前缀，实际调用使用各节标题中的完整工具名。

## 2 公共调用契约

编辑器一致性规则与执行边界见 [一致性约束](editor_consistency.md)。scene_list 的 revision 是编辑器会话内单调序号，任意场景／全局历史提交、Undo、Redo 都会使旧序号失效；不是 UndoRedo 的可回退版本号。属性编辑复用原生 Inspector 的关联属性、资源引用和插件钩子。编辑器基础设施对象只读，内部／外来节点、脚本与 Shader 源码不能借通用属性接口修改。


### 默认目录与扩展能力

`tools/list` 默认只展示 25 个高频工具。其他 38 个有状态编辑或专项检查工具仍注册在同一个目录中，通过 `godot_tools_discover` 按需发现，再用 `godot_tools_call` 调用，参数和版本校验与直接调用一致。

发现入口默认每页 20 个摘要、include_schema=false；需要某个工具的契约时，按 query/domains/tier 缩小范围并设置 include_schema=true。工具元数据 `_meta.godot/tier` 为 core 或 extended。启动 sidecar 时显式选择 `--toolset all` 可一次展示全部 63 个；默认 core 适合日常开发。

默认工具按工作环节分为以下几组：

| 环节 | 默认工具 |
| --- | --- |
| 能力与发现 | godot_capabilities_get、godot_tools_discover、godot_tools_call |
| 编辑器与诊断 | godot_editor_status、godot_editor_start、godot_editor_close、godot_editor_restart、godot_extension_reload、godot_diagnostics_read、godot_output_control |
| 文档协调 | godot_scene_list、godot_scene_tree、godot_filesystem_scan |
| 任务与运行 | godot_job_wait、godot_run_start、godot_run_list、godot_run_stop、godot_run_wait |
| 运行验证 | godot_object_inspect、godot_runtime_wait、godot_input_send、godot_input_release_all、godot_capture_frame |
| 调试 | godot_debugger_state、godot_debugger_control |

9 个 `godot_node_*` 单操作入口已整合进 `godot_edit_batch`，不提供旧别名。create、delete、rename、reparent、duplicate、attach、groups、connect、disconnect 均作为类型化 op 执行；没有丢失对应内存编辑和 Undo 能力。



### 2.1 执行位置

| 标记 | 执行位置 | 主要职责 |
| --- | --- | --- |
| S | 常驻 Python sidecar | 工具注册、任务、事件、产物、进程生命周期及连接编排 |
| E | Godot EditorPlugin 或现有编辑器调试器 | 场景文档、Undo、内存资源、缓冲区及调试控制 |
| R | 明确游戏实例中的 Runtime Helper | 运行对象、输入、UI、条件等待及 GPU 画面 |

执行位置描述处理链路；不能据此省略 session_id、文档版本或任务等待。标注“任务”的入口先返回 job_id，通过 job_get/job_wait 获取最终操作结果。

### 2.2 工程与目标身份

一个 sidecar 绑定一个工程。首次连接核对工程路径、插件版本、协议和源码哈希，防止对另一个工程或不匹配的插件包执行变更。

| 标识或参数 | 来源和用途 |
| --- | --- |
| editor_epoch | editor_status 返回的编辑器启动代次；完整重启后改变 |
| document_revision | editor_status 返回的整组打开文档版本；关闭或重启编辑器使用 |
| scene_id | scene_list/open/create 返回的场景文档身份，包含未命名场景 |
| revision | 单个场景的 Undo 历史版本，场景修改、Undo/Redo 后需要更新 |
| document_id 与 hash | script_read 返回的 CodeEdit 文档身份和缓冲区内容哈希 |
| settings_revision | project_settings_get 返回的项目内存设置整体版本 |
| session_id | run_list 或 run_start 成功结果返回的运行实例身份 |
| run_id | 当前 sidecar 自有游戏进程记录；用于定向等待和停止 |
| object_ref | 不透明对象句柄，绑定编辑器或运行实例，不能换成任意 instance_id |
| job_id 与 artifact_id | 当前 sidecar 登记的任务和产物，不能跨服务复用 |

支持编辑态与运行态的统一工具遵循以下二选一规则：

- 编辑态：context 可省略或为 editor，提供 scene_id。写入场景时还需 expected_revision；object_call 的两项受控动画库添加操作也需版本；其他编辑态非 const 方法不允许调用。object_release 是例外，不需要 scene_id。
- 运行态：必须显式提供 context=runtime 和 session_id，不提供 scene_id 或 expected_revision。运行态改值不持久化，也不加入编辑器 Undo。
- 节点路径与 object_ref 二选一。编辑态路径相对目标场景根，根为 `.`；运行态建议使用 `/root/...` 的明确路径。

scene_save、scene_close、节点选择及编辑态有副作用的方法调用要求目标文档已激活。可先通过 scene_open 激活。读取、属性编辑和场景 Undo/Redo 可明确指向非活动文档；不隐式把它替换为当前标签。

### 2.3 结果与变更语义

成功数据位于 MCP structuredContent；身份元数据位于 _meta.godot。编辑器结果包含工程、编辑器代次、请求和目标；sidecar 结果包含工程与服务身份。

| 字段 | 含义 |
| --- | --- |
| applied | 本次内存变更是否已应用 |
| undoable | 此操作是否进入对应编辑历史，不表示其所有外部副作用均可撤销 |
| saved | 是否实际写盘；内存变更成功不等于 saved=true |
| persistable | 可保存判断；返回 unknown 时应通过明确保存和重新打开验证 |
| revision | 操作后的场景版本，供下一次场景变更使用 |
| truncated | 本次结果不完整，应结合领域的分页和预算字段判断 |

失败通过 isError=true 和 text 中的 code/message/stage 返回。若出现 RESULT_CONTRACT_VIOLATION、超时或断线，变更可能已经发生，应重新读取状态核对，禁止自动重放非幂等修改。

### 2.4 任务与服务生命周期

| 状态 | 含义 |
| --- | --- |
| queued | 已登记，尚未执行 |
| running | 已开始执行 |
| succeeded | 操作实际成功，最终数据在 result |
| failed | 操作失败，查看 error 和任务输出 |
| cancelled | 在尚未执行时成功取消 |
| timed_out | 底层任务操作超时 |

后四项为终态。job_wait 的等待到期与底层任务 timed_out 不同；取消请求也不等于取消成功。

任务、事件、产物、输入持有者和自有运行记录依赖常驻 sidecar。逐条 CLI 调用会建立不同服务，不能用来跨调用等待任务、保存产物或持有输入。服务退出会删除临时产物并回收自有游戏，已经打开的编辑器继续保留。

### 2.5 类型化数值与对象

标量可使用 JSON 的 null、boolean、string 和可精确表达的有限数字。Godot 专用值使用带 _type 的对象，例如：

```json
{"_type":"Vector3","x":1,"y":2,"z":3}
```

| 标签 | 用途 |
| --- | --- |
| Vector2/Vector2i/Vector3/Vector3i/Vector4/Vector4i/Quaternion | 明确向量或四元数类型与分量 |
| Color | r/g/b/a 分量 |
| StringName / NodePath | 用 value 明确 Godot 名称或路径类型 |
| int64 | value 为规范的有符号 64 位十进制字符串，保留超过 JSON 客户端精确范围的整数 |
| float | value 为 nan、inf 或 -inf；不直接发送非有限 JSON 数字 |
| ObjectRef | 使用工具返回的 ref；不能自行构造任意引擎对象 ID |
| Resource | 输入可通过工程 path 引用资源；需要检查属性时使用返回的 ObjectRef |
| Array / Dictionary | 用 values 或 entries 显式表达嵌套 Godot 值 |
| VariantText | 使用带 type 的 Godot 文本表示处理变换和 Packed 值；不能用它绕过 Object/Array/Dictionary 显式协议 |

未知类型或错误字段类型会被拒绝。对象引用表有界且使用租约；Node 为弱引用，RefCounted 可被引用表保留。需要精确 Godot 类型的方法参数不能总用普通字符串替代 StringName 或 NodePath。

### 2.6 参数表阅读方式

每项工具的“主要参数”展示实际用途涉及的顶层字段和 Schema 数值范围。“条件”表示字段由目标上下文或操作分支决定是否必填，具体规则以该项说明及分支表为准。可选字段不表示能与其他分支参数任意组合。没有列为独立参数的编辑动作，应使用相应批量操作或其他工具组合完成。

## 3 工具目录总览


| 章节 | 工具数 | 主要能力 |
| --- | --- | --- |
| [4 基础能力与诊断](#4-基础能力与诊断) | 6 | 查询能力与构建身份、检索工具目录、通过注册表调用工具、查询编辑器状态、读取编辑器或任务诊断、操作 Output 面板 |
| [5 任务事件与产物](#5-任务事件与产物) | 5 | 查询任务快照、等待任务完成或变化、请求取消尚未执行的任务、读取任务和运行事件、分块读取产物 |
| [6 编辑器设置与类型反射](#6-编辑器设置与类型反射) | 4 | 读取项目内存设置、修改项目设置、查询已加载原生类型、选择编辑面板或场景节点 |
| [7 场景文档](#7-场景文档) | 8 | 列出打开的场景文档、打开或激活场景、创建未保存场景、保存场景文档、按策略关闭场景、读取编辑态或运行态场景树、撤销指定场景的一次操作、重做指定场景的一次操作 |
| [8 内存批量编辑](#8-内存批量编辑) | 1 | 组合执行场景编辑 |
| [9 对象查询与属性](#9-对象查询与属性) | 5 | 按类和名称查找对象、读取类型化对象属性、批量修改对象属性、调用反射方法、释放对象引用租约 |
| [10 编辑器资源](#10-编辑器资源) | 4 | 加载工程资源、创建原生内存资源、复制资源、保存资源文件 |
| [11 脚本文档与资源刷新](#11-脚本文档与资源刷新) | 5 | 读取编辑器脚本缓冲区、修改脚本缓冲区或撤销重做、重载关闭的项目脚本、扫描当前编辑器文件系统、重导入指定资源 |
| [12 编辑器生命周期](#12-编辑器生命周期) | 4 | 启动或复用编辑器、保护文档后关闭编辑器、重启并恢复保存的文档、核验原生扩展重载 |
| [13 运行实例](#13-运行实例) | 4 | 启动自有调试实例、列出运行和调试会话、停止服务自有实例、等待实例阶段或会话数量 |
| [14 输入等待与界面截图](#14-输入等待与界面截图) | 7 | 向指定实例发送输入、释放当前连接的输入、执行帧间隔输入序列、等待帧属性或信号条件、检查运行界面布局、设置运行控件焦点、捕获指定 GPU 画面 |
| [15 动画与骨骼姿态](#15-动画与骨骼姿态) | 4 | 列出动画资源、检查轨迹和关键帧、编辑自有动画资源、采样轨迹值或骨骼姿态 |
| [16 调试与性能](#16-调试与性能) | 6 | 设置脚本断点、清除脚本断点、读取暂停栈和变量、暂停继续或单步、读取引擎监控指标、采集原生性能帧 |
合计 63 个注册工具，其中默认 25 个、扩展 38 个。完整机器可读契约见 [tool_catalog.json](tool_catalog.json)。


## 4 基础能力与诊断

### 4.1 godot_capabilities_get 查询能力与构建身份

**入口层级**：默认工具。

获取当前工具集合、协议、插件源码哈希、Schema 哈希、提供者、引擎构建信息和实际原生接口探测结果。

**执行位置**：S，可读取 E 状态。

**参数**：无。

**核心结果**：tool_names、default_tool_names、active_toolset、workflow=file_first、version、protocol、package、schema_sha256、providers、editor、execution_modes 和 platform_limits。editor.connected=false 时同时说明原因。

**使用约束**：无参数。编辑器未连接也可返回静态能力；宣告的模式仍受本机图形会话与驱动条件限制，不能把模式名称当成渲染成功的证据。

**调用示例**

```json
{}
```

### 4.2 godot_tools_discover 检索工具目录

**入口层级**：默认工具。

按名称或描述关键字、领域和入口层级检索注册工具，适合按需加载工具说明。

**执行位置**：S。

**主要参数**

| 参数 | 要求 | 类型与范围 | 说明 |
| --- | --- | --- | --- |
| `domains` | 可选 | 数组；最多 32 项 | 工具元数据中的领域名称 |
| `include_schema` | 可选 | 布尔 | 返回输入与输出 Schema |
| `limit` | 可选 | 整数；1～128 | 本页条数 |
| `offset` | 可选 | 整数；0～无上限 | 偏移；产物按字节，其他列表按项 |
| `query` | 可选 | 字符串 | 名称或描述关键字 |
| `tier` | 可选 | 字符串；core / extended | 限定默认或扩展工具 |

**核心结果**：tools、total、next_offset、has_more；include_schema=true 时带输入与输出 Schema。

**使用约束**：query 为不区分大小写的子串；domains 匹配 _meta.godot/domain，tier 可为 core/extended。默认 offset=0、limit=20、include_schema=false；limit 范围 1～128。

**调用示例**

```json
{
  "query": "scene",
  "limit": 10,
  "include_schema": true
}
```

### 4.3 godot_tools_call 通过注册表调用工具

**入口层级**：默认工具。

提供统一的间接调用入口，使无法动态更新工具列表的客户端仍能调用已注册工具。

**执行位置**：S，转发至实际执行侧。

**主要参数**

| 参数 | 要求 | 类型与范围 | 说明 |
| --- | --- | --- | --- |
| `arguments` | 必填 | 对象 | 被调用工具的参数对象 |
| `tool_name` | 必填 | 字符串 | 要调用的工具名称 |

**核心结果**：被调用工具的结构化结果；失败时返回工具错误。

**使用约束**：tool_name 与 arguments 必填，arguments 使用目标工具自己的 Schema。禁止 tools_call 递归调用自身；不会绕过目标工具的版本、会话或输入检查。

**调用示例**

```json
{
  "tool_name": "godot_scene_list",
  "arguments": {}
}
```

### 4.4 godot_editor_status 查询编辑器状态

**入口层级**：默认工具。

确认连接的工程、编辑器代次、初始化状态、打开场景、未保存脚本、扫描状态和运行状态。

**执行位置**：S/E。

**参数**：无。

**核心结果**：connected；连接成功时返回 state，含 editor_pid、editor_epoch、document_revision、editor_ready、open_scenes、unsaved_scenes、unsaved_scripts、engine_build、package 等。连接失败返回 reason，后台启动时可含 startup_job。

**使用约束**：无参数。document_revision 用于整个编辑器关闭或重启；它覆盖场景、脚本及其他资源／插件脏状态；scene_list.revision 是会话级单调历史序号。connected=true 不应替代 editor_ready 检查。

**调用示例**

```json
{}
```

### 4.5 godot_diagnostics_read 读取编辑器或任务诊断

**入口层级**：默认工具。

从有界原生日志读取当前工程诊断，或定位指定任务的输出产物。

**执行位置**：S/E。

**主要参数**

| 参数 | 要求 | 类型与范围 | 说明 |
| --- | --- | --- | --- |
| `cursor` | 可选 | 整数；0～无上限 | 上次返回的增量读取游标 |
| `job_id` | 可选 | 字符串 | 当前服务的任务身份 |
| `levels` | 可选 | 数组；最多 5 项 | 日志级别过滤 |
| `max_entries` | 可选 | 整数；1～2048 | 诊断条数预算 |
| `session_id` | 可选 | 字符串 | 明确运行实例 |
| `source` | 可选 | 字符串；editor / runtime | 诊断来源过滤 |

**核心结果**：编辑器模式返回 entries、next_cursor、latest_cursor、oldest_cursor、has_more、dropped、editor_pid；任务模式返回 artifacts、state、exit_code。

**使用约束**：cursor 默认 0，max_entries 默认 200、上限 2048；levels 可为 info/editor/warning/error，source 可为 editor/runtime。session_id 过滤要求实例仍连接。原生运行日志可含 PID、线程、文件、行号与栈；编辑器消息不归因到某次请求。job_id 模式的文本需再由 artifact_read 读取。

**调用示例**

```json
{
  "cursor": 0,
  "levels": [
    "error",
    "warning"
  ],
  "max_entries": 200
}
```


### 4.6 godot_output_control 操作 Output 面板

**入口层级**：默认工具。

集中查询、配置、读取和清空真实 Output dock，区分面板显示和 MCP 诊断历史。

**执行位置**：E，复用原生 EditorLog。

**主要参数**

| 参数 | 要求 | 类型与范围 | 说明 |
| --- | --- | --- | --- |
| action | 可选 | get / configure / read / clear | 默认 get |
| visible | configure 可选 | 布尔 | 显示或收起 Output dock |
| levels | configure 可选 | 数组，最多 4 项 | info/error/warning/editor，空数组隐藏全部 |
| search | configure 可选 | 字符串，最多 1024 字符 | 原生面板子串搜索 |
| collapse | configure 可选 | 布尔 | 原生连续重复消息折叠 |
| offset / max_chars | read 可选 | 非负字符偏移；每页 1～65536 字符 | max_chars 默认 16384；面板超过 1 Mi 字符先筛选 |
| expected_revision / editor_epoch | read 后续页必填 | 文本版本 / 编辑器代次 | 阻止混合不同版本或不同进程的分页 |
| editor_epoch | clear 必填 | 字符串 | 预期编辑器代次 |
| scope | clear 可选 | panel / diagnostics / all | 默认 panel |
| through_cursor | diagnostics/all 必填 | 非负整数 | 只删除截止之前的诊断，保留之后的新记录 |

configure 至少指定一项配置，参数不能跨 action 混用。read 的后续页 offset > 0 时同时传 expected_revision 和 editor_epoch。

**核心结果**：状态含 visible、levels、search、collapse、counts、message_count、stored_rows、line_limit、text_characters、revision、diagnostics、editor_epoch。read 返回 text、next_offset、total_characters、has_more、revision 与 editor_epoch；clear 另含 cleared_panel、cleared_diagnostics、removed_diagnostics。

**使用约束**：面板筛选不影响结构化诊断读取；panel 清空不删除诊断，diagnostics 清空不改变面板。all 清面板并清到指定诊断游标。诊断序号保持单调，history_generation 标记显式历史清空，dropped 标记旧游标失去数据。Output 计数不等于 Debugger 独立错误树的计数。操作共享当前工程面板，不支持按实例清面板。

**调用示例**

```json
{"action":"get"}
```

配置、分页与三种清空范围的完整示例见 [Output 操作指南](output_control.md)。


## 5 任务事件与产物

### 5.1 godot_job_get 查询任务快照

**入口层级**：扩展工具，按需发现和调用。

读取当前服务登记的会话任务，检查操作是否已经实际完成。

**执行位置**：S。

**主要参数**

| 参数 | 要求 | 类型与范围 | 说明 |
| --- | --- | --- | --- |
| `job_id` | 必填 | 字符串 | 当前服务的任务身份 |

**核心结果**：job_id、kind、state、sequence、artifacts；成功终态含 result，失败终态含 error。

**使用约束**：job_id 必须属于当前常驻 sidecar。支持的状态及终态含义见公共契约；编辑器退出不删除服务仍持有的任务。

**调用示例**

```json
{
  "job_id": "<job_id>"
}
```

### 5.2 godot_job_wait 等待任务完成或变化

**入口层级**：默认工具。

按条件变量通知等待任务终态，或等待该任务的 sequence 超过指定值。

**执行位置**：S。

**主要参数**

| 参数 | 要求 | 类型与范围 | 说明 |
| --- | --- | --- | --- |
| `job_id` | 必填 | 字符串 | 当前服务的任务身份 |
| `timeout_ms` | 必填 | 整数；1～30000 | 本次操作或等待期限，单位毫秒 |
| `after_sequence` | 条件 | 整数；0～无上限 | 此前观察到的任务序号 |
| `until` | 条件 | 字符串；terminal / changed | 等待终态或状态序号变化 |

**核心结果**：当前任务快照，与 job_get 相同。

**使用约束**：job_id、timeout_ms 必填，timeout_ms 为 1～30000。until 默认 terminal；until=changed 必须提供 after_sequence。等待到期可以返回 running，既不取消任务，也不表示任务失败。

**调用示例**

```json
{
  "job_id": "<job_id>",
  "until": "terminal",
  "timeout_ms": 10000
}
```

### 5.3 godot_job_cancel 请求取消尚未执行的任务

**入口层级**：扩展工具，按需发现和调用。

在任务尚未执行时请求取消，并报告取消是否被接受。

**执行位置**：S。

**主要参数**

| 参数 | 要求 | 类型与范围 | 说明 |
| --- | --- | --- | --- |
| `job_id` | 必填 | 字符串 | 当前服务的任务身份 |

**核心结果**：任务快照、cancellation_accepted；接受时有 side_effects_applied=false，拒绝时说明 reason。

**使用约束**：只接受 queued 阶段。运行中的不可中断操作与已经终止的任务返回 cancellation_accepted=false，不把拒绝取消伪装成 cancelled。

**调用示例**

```json
{
  "job_id": "<job_id>"
}
```

### 5.4 godot_events_read 读取任务和运行事件

**入口层级**：扩展工具，按需发现和调用。

增量读取服务事件，可按任务、实例和事件类型过滤，必要时等待下一条匹配事件。

**执行位置**：S。

**主要参数**

| 参数 | 要求 | 类型与范围 | 说明 |
| --- | --- | --- | --- |
| `cursor` | 可选 | 整数；0～无上限 | 上次返回的增量读取游标 |
| `job_id` | 可选 | 字符串 | 当前服务的任务身份 |
| `kinds` | 可选 | 数组；最多 3 项 | 事件类型过滤 |
| `session_id` | 可选 | 字符串 | 明确运行实例 |
| `wait_ms` | 可选 | 整数；0～30000 | 事件长等待期限，单位毫秒 |

**核心结果**：events、next_cursor、dropped；事件含 sequence、kind，以及相应 job/run/session 字段。

**使用约束**：kinds 为 job_state/run_stage/run_exited；wait_ms 为 0～30000，默认 0。cursor 是整个服务的全局序号；即使过滤后为空也应推进 next_cursor。事件环最多 2048 条，dropped 表示历史已覆盖。

**调用示例**

```json
{
  "cursor": 0,
  "kinds": [
    "job_state"
  ],
  "wait_ms": 1000
}
```

### 5.5 godot_artifact_read 分块读取产物

**入口层级**：扩展工具，按需发现和调用。

读取服务登记的截图或任务输出，避免任意路径读取。

**执行位置**：S。

**主要参数**

| 参数 | 要求 | 类型与范围 | 说明 |
| --- | --- | --- | --- |
| `artifact_id` | 必填 | 字符串 | 登记产物身份 |
| `length` | 可选 | 整数；1～1048576 | 单次读取的最大字节数，动画中另按用途解释 |
| `offset` | 可选 | 整数；0～无上限 | 偏移；产物按字节，其他列表按项 |

**核心结果**：artifact_id、mime_type、encoding=base64、data、next_offset、size、complete、sha256、storage。

**使用约束**：offset 按字节计，默认 0；length 默认 65536、上限 1048576。complete 表示产物写入已结束，读到末尾需比较 next_offset 与 size。产物在当前服务退出时删除；不接受文件系统路径作为 artifact_id。

**调用示例**

```json
{
  "artifact_id": "<artifact_id>",
  "offset": 0,
  "length": 65536
}
```


## 6 编辑器设置与类型反射

### 6.1 godot_project_settings_get 读取项目内存设置

**入口层级**：扩展工具，按需发现和调用。

读取编辑器当前 ProjectSettings 的指定键，以及用于后续修改和保存的版本信息。

**执行位置**：E。

**主要参数**

| 参数 | 要求 | 类型与范围 | 说明 |
| --- | --- | --- | --- |
| `keys` | 必填 | 数组 | 项目设置键数组 |

**核心结果**：settings 中各键的 exists 和类型化 value；settings_revision、project.godot 的 disk_hash。

**使用约束**：keys 必填。不存在的键返回 exists=false。值来自当前编辑器内存，不能把它当作磁盘文件的独立读取结果。

**调用示例**

```json
{
  "keys": [
    "application/config/name"
  ]
}
```

### 6.2 godot_project_settings_set 修改项目设置

**入口层级**：扩展工具，按需发现和调用。

先检查设置整体版本，再预检并修改多个设置；可选择写入 project.godot。

**执行位置**：E。

**主要参数**

| 参数 | 要求 | 类型与范围 | 说明 |
| --- | --- | --- | --- |
| `changes` | 必填 | 对象 | 属性或设置键到类型化值的映射 |
| `expected_revision` | 必填 | 字符串 | 项目设置整体版本字符串，不是场景整数版本 |
| `expected_disk_hash` | 可选 | 字符串 | 目标文件当前 SHA256 |
| `save` | 可选 | 布尔 | 写入项目设置文件 |

**核心结果**：applied、saved、undoable=true、keys、settings_revision_before、settings_revision、disk_hash、restart_requirements。

**使用约束**：expected_revision 必须使用 project_settings_get 的 settings_revision 字符串。save 默认 false；save=true 必须给当前 expected_disk_hash。最多修改 256 键；保存失败会撤销本次修改；使用全局 Undo 历史，不标记场景为脏。原生 General Inspector 隐藏／只读／内部设置，以及 InputMap、插件、autoload、导入等专用模型交给相应原生面板或离线文件流程。

**调用示例**

```json
{
  "changes": {
    "application/config/name": "Automation Preview"
  },
  "expected_revision": "<settings_revision>",
  "save": false
}
```

### 6.3 godot_class_describe 查询已加载原生类型

**入口层级**：扩展工具，按需发现和调用。

查询当前 ClassDB 中类的属性、方法、信号和反射签名，包括已加载 GDExtension 注册类。

**执行位置**：E。

**主要参数**

| 参数 | 要求 | 类型与范围 | 说明 |
| --- | --- | --- | --- |
| `class_name` | 必填 | 字符串 | 原生类名称 |

**核心结果**：class、properties、methods、signals。

**使用约束**：class_name 必填。该入口描述 ClassDB；脚本对象的实际属性应使用 object_inspect，不承诺任意 C++ 私有字段反射。

**调用示例**

```json
{
  "class_name": "Control"
}
```

### 6.4 godot_editor_select 选择编辑面板或场景节点

**入口层级**：扩展工具，按需发现和调用。

通过 EditorInterface 切换主要编辑面板，或更新明确场景中的节点选择。

**执行位置**：E。

**主要参数**

| 参数 | 要求 | 类型与范围 | 说明 |
| --- | --- | --- | --- |
| `node_paths` | 可选 | 数组 | 节点路径数组 |
| `panel` | 可选 | 字符串；2d / 3d / script / game | 编辑器主要面板 |
| `scene_id` | 可选 | 字符串 | 明确编辑场景 |

**核心结果**：面板模式返回 selected_panel；节点模式返回 selected_nodes 数量。

**使用约束**：panel 可为 2d/3d/script/game。节点模式使用 scene_id 与 node_paths，要求该文档已激活；不操作操作系统窗口。两种用途应分别调用，不在同一请求混用。

**调用示例**

```json
{
  "scene_id": "<scene_id>",
  "node_paths": [
    "."
  ]
}
```


## 7 场景文档

### 7.1 godot_scene_list 列出打开的场景文档

**入口层级**：默认工具。

获取所有打开场景的身份、版本、保存路径、活动状态和未保存状态。

**执行位置**：E。

**参数**：无。

**核心结果**：scenes，每项有 scene_id、revision、path、active、dirty；active 为当前文档或 null。

**使用约束**：无参数。未保存场景也有 scene_id，path 可以为空。旧 ID 在引用过期或编辑器重启后不能继续使用，应重新查询。

**调用示例**

```json
{}
```

### 7.2 godot_scene_open 打开或激活场景

**入口层级**：扩展工具，按需发现和调用。

通过编辑器打开项目场景；目标已经打开时切换到对应文档。

**执行位置**：E。

**主要参数**

| 参数 | 要求 | 类型与范围 | 说明 |
| --- | --- | --- | --- |
| `path` | 必填 | 字符串 | 工程内资源或脚本路径 |

**核心结果**：scene_id、revision。

**使用约束**：path 必须是可加载为 PackedScene 的 res:// 路径。当前版本会激活目标，没有后台不激活的参数。

**调用示例**

```json
{
  "path": "res://scenes/Preview.tscn"
}
```

### 7.3 godot_scene_create 创建未保存场景

**入口层级**：扩展工具，按需发现和调用。

以原生 Node 派生类创建场景根，并加入编辑器文档。

**执行位置**：E。

**主要参数**

| 参数 | 要求 | 类型与范围 | 说明 |
| --- | --- | --- | --- |
| `root_name` | 必填 | 字符串 | 场景根节点名 |
| `root_type` | 必填 | 字符串 | 场景根节点的原生类型 |

**核心结果**：scene_id、revision、saved=false。

**使用约束**：root_type 必须是可实例化的原生 Node 类；root_name 必须是有效节点名。该工具不接受保存路径，创建后单独调用 scene_save。

**调用示例**

```json
{
  "root_type": "Node3D",
  "root_name": "Preview"
}
```

### 7.4 godot_scene_save 保存场景文档

**入口层级**：扩展工具，按需发现和调用。

核对目标文档及历史版本，执行编辑器保存并核验写盘结果。

**执行位置**：E。

**主要参数**

| 参数 | 要求 | 类型与范围 | 说明 |
| --- | --- | --- | --- |
| `expected_revision` | 必填 | 整数 | 当前目标的预期版本 |
| `scene_id` | 必填 | 字符串 | 明确编辑场景 |
| `overwrite` | 可选 | 布尔 | 明确允许覆盖已有目标 |
| `path` | 可选 | 字符串 | 工程内资源或脚本路径 |

**核心结果**：saved、path、hash、revision。

**使用约束**：必须激活目标，不能另存覆盖另一个打开的场景文档。新场景需给 res:// 下 .tscn 路径；已保存场景可省略 path。另存到已有文件时需要 overwrite=true。hash 是实际保存文件的 SHA256；这里只保存场景，不替代外部资源保存。

**调用示例**

```json
{
  "scene_id": "<scene_id>",
  "expected_revision": 7,
  "path": "res://preview.tscn"
}
```

### 7.5 godot_scene_close 按策略关闭场景

**入口层级**：扩展工具，按需发现和调用。

在指定场景的版本匹配后关闭当前活动文档，并明确处理未保存内容。

**执行位置**：E。

**主要参数**

| 参数 | 要求 | 类型与范围 | 说明 |
| --- | --- | --- | --- |
| `expected_revision` | 必填 | 整数 | 当前目标的预期版本 |
| `scene_id` | 必填 | 字符串 | 明确编辑场景 |
| `unsaved` | 可选 | 字符串；reject / save / discard | 未保存内容处理策略 |

**核心结果**：closed=true。

**使用约束**：unsaved 默认 reject，可为 save/discard。未命名场景不能通过 save 策略自动猜测路径，需先 scene_save。discard 表示明确放弃修改。

**调用示例**

```json
{
  "scene_id": "<scene_id>",
  "expected_revision": 7,
  "unsaved": "reject"
}
```

### 7.6 godot_scene_tree 读取编辑态或运行态场景树

**入口层级**：默认工具。

读取目标子树的有界元信息和对象引用，使用快照分页避免分页期间结构变化造成混淆。

**执行位置**：E 或 E/R。

**主要参数**

| 参数 | 要求 | 类型与范围 | 说明 |
| --- | --- | --- | --- |
| `context` | 条件 | 字符串；editor / runtime | 编辑态或运行态，遵循公共目标规则 |
| `scene_id` | 条件 | 字符串 | 明确编辑场景 |
| `session_id` | 条件 | 字符串 | 明确运行实例 |
| `limit` | 可选 | 整数；1～1024 | 本页条数 |
| `offset` | 可选 | 整数；0～无上限 | 偏移；产物按字节，其他列表按项 |
| `root_path` | 可选 | 字符串 | 查询子树根路径 |
| `snapshot` | 可选 | 字符串 | 首个分页请求返回的快照身份 |

**核心结果**：nodes、snapshot、snapshot_revision、next_offset、total、has_more、source_truncated、truncated；节点含路径、原生类、组、owner、实例来源和对象引用。

**使用约束**：使用编辑态或运行态公共目标。limit 默认 128、上限 1024。分页重复相同目标与 root_path，并传 snapshot/offset。编辑态版本变化使快照失效；运行态元信息冻结，节点释放后标 expired。

**调用示例**

```json
{
  "scene_id": "<scene_id>",
  "root_path": ".",
  "limit": 128
}
```

### 7.7 godot_scene_undo 撤销指定场景的一次操作

**入口层级**：扩展工具，按需发现和调用。

操作指定文档的 Undo 历史，恢复上一条场景编辑。

**执行位置**：E。

**主要参数**

| 参数 | 要求 | 类型与范围 | 说明 |
| --- | --- | --- | --- |
| `expected_revision` | 必填 | 整数 | 当前目标的预期版本 |
| `scene_id` | 必填 | 字符串 | 明确编辑场景 |

**核心结果**：applied、revision、saved=false。

**使用约束**：每次调用撤销一条，不提供 steps 参数；使用当前 expected_revision。非活动文档历史也可指定操作；保存和外部副作用不属于本次 Undo。

**调用示例**

```json
{
  "scene_id": "<scene_id>",
  "expected_revision": 7
}
```

### 7.8 godot_scene_redo 重做指定场景的一次操作

**入口层级**：扩展工具，按需发现和调用。

重新执行指定文档刚被撤销的一条场景编辑。

**执行位置**：E。

**主要参数**

| 参数 | 要求 | 类型与范围 | 说明 |
| --- | --- | --- | --- |
| `expected_revision` | 必填 | 整数 | 当前目标的预期版本 |
| `scene_id` | 必填 | 字符串 | 明确编辑场景 |

**核心结果**：applied、revision、saved=false。

**使用约束**：每次调用重做一条；使用 Undo 后返回的新 revision。没有可重做历史时返回错误。

**调用示例**

```json
{
  "scene_id": "<scene_id>",
  "expected_revision": 7
}
```


## 8 内存批量编辑

### 8.1 godot_edit_batch 组合执行场景编辑

**入口层级**：扩展工具，按需发现和调用。

预检同一文档的多个类型化编辑，支持临时节点引用，并组合成一次 Undo。

**执行位置**：E。

**主要参数**

| 参数 | 要求 | 类型与范围 | 说明 |
| --- | --- | --- | --- |
| `expected_revision` | 必填 | 整数；0～无上限 | 当前目标的预期版本 |
| `operations` | 必填 | 数组；最多 256 项 | 类型化批量操作数组 |
| `scene_id` | 必填 | 字符串 | 明确编辑场景 |
| `dry_run` | 可选 | 布尔 | 仅预检，不提交编辑 |

**核心结果**：applied、undoable、saved=false、atomic=false、operations、references、revision；dry_run 时不提交。

**使用约束**：operations 为 1～256 项，dry_run 默认 false。支持 create/instance_scene/duplicate/delete/rename/reparent/groups/set/attach/connect/disconnect。临时 id 以 $id 引用，可跟随本批次改名与移动。用户 setter、脚本与外部副作用不保证回滚，因此不承诺原子事务。

**调用示例**

```json
{
  "scene_id": "<scene_id>",
  "expected_revision": 7,
  "operations": [
    {
      "op": "create",
      "id": "probe",
      "type": "Node3D",
      "name": "Probe"
    },
    {
      "op": "set",
      "node_path": "$probe",
      "changes": {
        "position": {
          "_type": "Vector3",
          "x": 1,
          "y": 2,
          "z": 3
        }
      }
    }
  ]
}
```


**批量操作分支**

| op | 分支必填字段 | 补充字段与行为 |
| --- | --- | --- |
| create | name | type、parent_path、id；创建原生节点 |
| instance_scene | path、name | parent_path、id；实例化 PackedScene 并保留实例关联 |
| duplicate | node_path、name | parent_path、id；复制子树 |
| delete | node_path | 删除明确子节点 |
| rename | node_path、name | 检查唯一名称，引用路径需另行更新 |
| reparent | node_path、parent_path | index、keep_global_transform |
| groups | node_path | add、remove 组数组 |
| set | node_path、changes | 类型化属性映射，最多 256 项 |
| attach | node_path、script_path | 脚本路径或 null |
| connect | node_path、signal_name、receiver_path、method | binds、unbind、flags |
| disconnect | node_path、signal_name、receiver_path、method | 用 binds、unbind 精确匹配连接 |

id 只可用于 create、instance_scene、duplicate，必须在本批次唯一且是有效标识。后续使用 `$id` 或 `$id/Child` 引用，不能引用已删除的对象。保存、扫描和脚本执行不加入这个 Undo 批次。


## 9 对象查询与属性

### 9.1 godot_object_query 按类和名称查找对象

**入口层级**：扩展工具，按需发现和调用。

在指定树范围内按原生类和名称模式查找节点，使用与 scene_tree 相同的快照分页协议。

**执行位置**：E 或 E/R。

**主要参数**

| 参数 | 要求 | 类型与范围 | 说明 |
| --- | --- | --- | --- |
| `context` | 条件 | 字符串；editor / runtime | 编辑态或运行态，遵循公共目标规则 |
| `scene_id` | 条件 | 字符串 | 明确编辑场景 |
| `session_id` | 条件 | 字符串 | 明确运行实例 |
| `class_name` | 可选 | 字符串 | 原生类名称 |
| `limit` | 可选 | 整数；1～1024 | 本页条数 |
| `name_pattern` | 可选 | 字符串 | 节点名通配模式 |
| `offset` | 可选 | 整数；0～无上限 | 偏移；产物按字节，其他列表按项 |
| `root_path` | 可选 | 字符串 | 查询子树根路径 |
| `snapshot` | 可选 | 字符串 | 首个分页请求返回的快照身份 |

**核心结果**：nodes 和快照分页字段，每项含对象引用。

**使用约束**：class_name 使用 Node.is_class 的原生类型判定，name_pattern 使用 Godot 通配模式，默认 *。不提供任意表达式、属性谓词或磁盘搜索。

**调用示例**

```json
{
  "scene_id": "<scene_id>",
  "class_name": "Control",
  "name_pattern": "*Button",
  "limit": 64
}
```

### 9.2 godot_object_inspect 读取类型化对象属性

**入口层级**：默认工具。

按节点路径或对象引用检查属性，返回可继续展开的 Resource/RefCounted 引用。

**执行位置**：E 或 E/R。

**主要参数**

| 参数 | 要求 | 类型与范围 | 说明 |
| --- | --- | --- | --- |
| `context` | 条件 | 字符串；editor / runtime | 编辑态或运行态，遵循公共目标规则 |
| `scene_id` | 条件 | 字符串 | 明确编辑场景 |
| `session_id` | 条件 | 字符串 | 明确运行实例 |
| `max_properties` | 可选 | 整数；1～256 | 属性返回预算 |
| `node_path` | 可选 | 字符串 | 目标节点路径 |
| `object_ref` | 可选 | 字符串 | 工具返回的对象句柄 |
| `properties` | 可选 | 数组 | 属性名或姿态属性选择器，按入口解释 |

**核心结果**：object、properties；每项有 name、type、value。编辑态还可含 revision。

**使用约束**：properties 指定属性名；省略则按 max_properties 预算读取，默认 128、上限 256。不读取任意私有字段。引用需绑定正确编辑器或实例；资源引用在编辑态也需要 scene_id 上下文。

**调用示例**

```json
{
  "scene_id": "<scene_id>",
  "node_path": ".",
  "properties": [
    "name",
    "position"
  ]
}
```

### 9.3 godot_object_set_properties 批量修改对象属性

**入口层级**：扩展工具，按需发现和调用。

先按反射信息解码并检查属性类型，再应用 changes。

**执行位置**：E 或 E/R。

**主要参数**

| 参数 | 要求 | 类型与范围 | 说明 |
| --- | --- | --- | --- |
| `changes` | 必填 | 对象 | 属性或设置键到类型化值的映射 |
| `context` | 条件 | 字符串；editor / runtime | 编辑态或运行态，遵循公共目标规则 |
| `expected_revision` | 条件 | 整数；0～无上限 | 当前目标的预期版本 |
| `scene_id` | 条件 | 字符串 | 明确编辑场景 |
| `session_id` | 条件 | 字符串 | 明确运行实例 |
| `node_path` | 可选 | 字符串 | 目标节点路径 |
| `object_ref` | 可选 | 字符串 | 工具返回的对象句柄 |

**核心结果**：applied、saved、undoable；编辑态返回 revision，资源修改可返回共享影响说明与 persistable=unknown。

**使用约束**：changes 为 1～256 项。编辑态必须给 expected_revision 并加入 Undo；运行态禁止传编辑器版本，只影响当前实例。script/owner/name 使用专用编辑工具；导入资源先复制。外部共享资源需要单独 resource_save。

**调用示例**

```json
{
  "scene_id": "<scene_id>",
  "expected_revision": 7,
  "node_path": "Probe",
  "changes": {
    "position": {
      "_type": "Vector3",
      "x": 1,
      "y": 2,
      "z": 3
    }
  }
}
```

### 9.4 godot_object_call 调用反射方法

**入口层级**：扩展工具，按需发现和调用。

按真实方法签名、默认参数和 Variant 类型检查参数，再调用指定对象的方法。

**执行位置**：E 或 E/R。

**主要参数**

| 参数 | 要求 | 类型与范围 | 说明 |
| --- | --- | --- | --- |
| `method` | 必填 | 字符串 | 反射方法或信号接收方法 |
| `context` | 条件 | 字符串；editor / runtime | 编辑态或运行态，遵循公共目标规则 |
| `scene_id` | 条件 | 字符串 | 明确编辑场景 |
| `session_id` | 条件 | 字符串 | 明确运行实例 |
| `allow_mutation` | 可选 | 布尔 | 明确允许非 const 方法副作用 |
| `arguments` | 可选 | 数组；最多 64 项 | 反射方法参数数组 |
| `expected_revision` | 可选 | 整数；0～无上限 | 当前目标的预期版本 |
| `node_path` | 可选 | 字符串 | 目标节点路径 |
| `object_ref` | 可选 | 字符串 | 工具返回的对象句柄 |

**核心结果**：value、mutating、undoable、saved=false。

**使用约束**：arguments 最多 64 项。编辑态仅允许公开 const 方法；allow_mutation 不能绕过此限制。唯一受控例外为 AnimationLibrary.add_animation 和 AnimationPlayer.add_animation_library，要求版本校验并记录原生逆操作（undoable=true）；不能替换已有同名项。运行态非 const 方法仍需 allow_mutation=true，并不提供 Undo。不允许 free/queue_free/set_script/call/callv/call_deferred 等入口。

**调用示例**

```json
{
  "scene_id": "<scene_id>",
  "node_path": ".",
  "method": "get_class"
}
```

### 9.5 godot_object_release 释放对象引用租约

**入口层级**：扩展工具，按需发现和调用。

主动释放插件引用表中的不透明句柄，减少引用占用。

**执行位置**：E 或 E/R。

**主要参数**

| 参数 | 要求 | 类型与范围 | 说明 |
| --- | --- | --- | --- |
| `object_refs` | 必填 | 数组 | 需要释放的引用数组 |
| `context` | 条件 | 字符串；editor / runtime | 编辑态或运行态，遵循公共目标规则 |
| `session_id` | 条件 | 字符串 | 明确运行实例 |

**核心结果**：released，为实际释放的引用数。

**使用约束**：object_refs 必填；编辑态无需 scene_id，运行态必须 context=runtime 与 session_id。释放不等于销毁 Node；失效或重复句柄不重复计数。

**调用示例**

```json
{
  "object_refs": [
    "<object_ref>"
  ]
}
```


## 10 编辑器资源

### 10.1 godot_resource_load 加载工程资源

**入口层级**：扩展工具，按需发现和调用。

加载 res:// 工程资源，返回有界可展开的引用。

**执行位置**：E。

**主要参数**

| 参数 | 要求 | 类型与范围 | 说明 |
| --- | --- | --- | --- |
| `path` | 必填 | 字符串 | 工程内资源或脚本路径 |

**核心结果**：resource，含 ObjectRef；另有资源 path。

**使用约束**：path 必填；当前公开契约没有 uid、cache_mode 或 context 参数。此入口使用编辑器 ResourceLoader，不是离线依赖扫描。

**调用示例**

```json
{
  "path": "res://themes/project_theme.tres"
}
```

### 10.2 godot_resource_create 创建原生内存资源

**入口层级**：扩展工具，按需发现和调用。

实例化可创建的原生 Resource 类，供后续赋值、属性修改或保存。

**执行位置**：E。

**主要参数**

| 参数 | 要求 | 类型与范围 | 说明 |
| --- | --- | --- | --- |
| `class_name` | 必填 | 字符串 | 原生类名称 |

**核心结果**：resource、saved=false。

**使用约束**：class_name 可为 Theme、StyleBoxFlat、材质等实际可实例化 Resource 派生类。新资源仅存在内存，必须赋给可保存场景或显式保存才能持久化。

**调用示例**

```json
{
  "class_name": "Theme"
}
```

### 10.3 godot_resource_duplicate 复制资源

**入口层级**：扩展工具，按需发现和调用。

复制当前编辑器持有的 Resource，显式选择浅复制或包含子资源的复制。

**执行位置**：E。

**主要参数**

| 参数 | 要求 | 类型与范围 | 说明 |
| --- | --- | --- | --- |
| `object_ref` | 必填 | 字符串 | 工具返回的对象句柄 |
| `deep` | 可选 | 布尔 | 是否包含子资源的复制 |

**核心结果**：resource、saved=false、resource_policy、warnings。

**使用约束**：object_ref 必填，deep 默认 false。深复制遵循 Godot 对外部引用与自定义 Resource 的规则，不能假设所有引用都已完全隔离；修改前应检查副本。

**调用示例**

```json
{
  "object_ref": "<object_ref>",
  "deep": true
}
```

### 10.4 godot_resource_save 保存资源文件

**入口层级**：扩展工具，按需发现和调用。

将内存 Resource 保存到工程路径，并返回文件身份与实际写盘信息。

**执行位置**：E。

**主要参数**

| 参数 | 要求 | 类型与范围 | 说明 |
| --- | --- | --- | --- |
| `object_ref` | 必填 | 字符串 | 工具返回的对象句柄 |
| `path` | 必填 | 字符串 | 工程内资源或脚本路径 |
| `expected_disk_hash` | 可选 | 字符串 | 目标文件当前 SHA256 |
| `overwrite` | 可选 | 布尔 | 明确允许覆盖已有目标 |

**核心结果**：saved、path、hash、uid。

**使用约束**：object_ref/path 必填。目标已存在时需 overwrite=true 和当前 expected_disk_hash；不匹配返回 DISK_CONFLICT。仅接受 .tres/.res，通过 EditorNode 保存并发出原生保存通知。禁止覆盖另一个已加载资源的路径；已命名资源不能直接改路径，需先复制。Script、Shader、PackedScene、EditorSettings 使用所属文档流程保存。

**调用示例**

```json
{
  "object_ref": "<object_ref>",
  "path": "res://debug_theme.tres"
}
```


结构批量编辑与动画编辑在存在活动游戏会话时返回 LIVE_EDIT_UNSUPPORTED；先 run_stop 再修改，或使用明确的 runtime 入口执行临时游戏修改。

## 11 脚本文档与资源刷新

### 11.1 godot_script_read 读取编辑器脚本缓冲区

**入口层级**：扩展工具，按需发现和调用。

读取已打开 CodeEdit 文档的内存源码和编辑状态，可明确请求打开现有脚本。

**执行位置**：E。

**主要参数**

| 参数 | 要求 | 类型与范围 | 说明 |
| --- | --- | --- | --- |
| `path` | 必填 | 字符串 | 工程内资源或脚本路径 |
| `open_if_needed` | 可选 | 布尔 | 允许打开现有脚本编辑文档 |

**核心结果**：path、document_id、source、source_kind=editor、hash、revision、dirty、disk_hash、differs_from_disk。

**使用约束**：path 必填，open_if_needed 默认 false。没有磁盘读取回退；脚本文档最多 1 MiB。document_id 是当前缓冲区句柄，关闭或重启后不能沿用。

**调用示例**

```json
{
  "path": "res://scripts/probe.gd",
  "open_if_needed": true
}
```

### 11.2 godot_script_patch 修改脚本缓冲区或撤销重做

**入口层级**：扩展工具，按需发现和调用。

基于文档身份和文本哈希精确替换编辑器源码，保留 CodeEdit 的编辑历史。

**执行位置**：E。

**主要参数**

| 参数 | 要求 | 类型与范围 | 说明 |
| --- | --- | --- | --- |
| `document_id` | 必填 | 字符串 | CodeEdit 文档句柄 |
| `expected_hash` | 必填 | 字符串 | 当前脚本缓冲区内容哈希 |
| `action` | 条件 | 字符串；apply / undo / redo | 操作分支或 InputMap 动作，按入口解释 |
| `edits` | 条件 | 数组；最多 256 项 | 精确 before/after 文本替换数组 |
| `expected_revision` | 可选 | 整数；0～无上限 | 当前目标的预期版本 |

**核心结果**：applied、saved=false、undoable=true、document、validation=editor_deferred。

**使用约束**：document_id/expected_hash 必填，expected_revision 可附加。action 默认 apply；apply 需 edits，每个 before 在逐步演变的缓冲区中必须恰好出现一次。undo/redo 不带 edits。本工具不写磁盘，也不声称同步编译成功。

**调用示例**

```json
{
  "document_id": "<document_id>",
  "expected_hash": "0000000000000000000000000000000000000000000000000000000000000000",
  "action": "apply",
  "edits": [
    {
      "before": "var speed := 1.0",
      "after": "var speed := 2.0"
    }
  ]
}
```

### 11.3 godot_script_reload 重载关闭的项目脚本

**入口层级**：扩展工具，按需发现和调用。

重新加载编辑器缓存中的指定磁盘脚本，并给出逐脚本结果。

**执行位置**：E。

**主要参数**

| 参数 | 要求 | 类型与范围 | 说明 |
| --- | --- | --- | --- |
| `paths` | 必填 | 数组；最多 256 项 | 明确的工程资源或脚本路径数组 |

**核心结果**：scripts、runtime_updated=false。

**使用约束**：paths 为 1～256 个 res:// 脚本；任一目标仍打开为编辑文档时拒绝，避免覆盖缓冲区。不会热重建游戏对象或 UI；运行中更新使用受控 stop/start。

**调用示例**

```json
{
  "paths": [
    "res://scripts/probe.gd"
  ]
}
```

### 11.4 godot_filesystem_scan 扫描当前编辑器文件系统

**入口层级**：默认工具。

调用现有编辑器扫描并等待完成信号，使外部工具写入的工程文件进入编辑器资源视图。

**执行位置**：E。

**参数**：无。

**核心结果**：scanned=true、scanning。

**使用约束**：无参数。扫描与指定资源重导入、脚本重载、运行实例更新是不同操作，按需分别调用。

**调用示例**

```json
{}
```

### 11.5 godot_resource_reimport 重导入指定资源

**入口层级**：扩展工具，按需发现和调用。

对已知导入资源调用当前编辑器的重导入 API，并在完成后检查资源可加载性及导入期间错误。

**执行位置**：S/E，任务。

**主要参数**

| 参数 | 要求 | 类型与范围 | 说明 |
| --- | --- | --- | --- |
| `paths` | 必填 | 数组；最多 256 项 | 明确的工程资源或脚本路径数组 |

**核心结果**：先返回任务；任务成功结果含 completed、paths、resources、errors_since_start、diagnostics_dropped、success。资源项含 path、loaded、class、uid。

**使用约束**：paths 为 1～256 个现有 res:// 源文件，需要已有 .import 元信息。资源尚未被识别时先扫描。必须等待任务终态；任一加载或导入错误不能当作成功。开发期离线批量导入仍可走原生无头流程。

**调用示例**

```json
{
  "paths": [
    "res://assets/icon.svg"
  ]
}
```


## 12 编辑器生命周期

### 12.1 godot_editor_start 启动或复用编辑器

**入口层级**：默认工具。

复用当前工程的匹配编辑器，或用已配置的定制引擎启动新编辑器，并等待认证端点及初始文档恢复就绪。

**执行位置**：S/E，任务。

**主要参数**

| 参数 | 要求 | 类型与范围 | 说明 |
| --- | --- | --- | --- |
| `display_mode` | 可选 | 字符串；headless_logic / visible | 进程显示模式 |
| `reuse` | 可选 | 布尔 | 是否复用已匹配编辑器 |
| `timeout_ms` | 可选 | 整数；1～3600000 | 本次操作或等待期限，单位毫秒 |

**核心结果**：任务成功结果含 reused、state；新进程还报告 display_mode 和启动日志 log_artifact，可通过 artifact_read 分页读取。

**使用约束**：需 sidecar 配置工程与引擎路径。reuse 默认 true；display_mode 为 headless_logic/visible，默认 headless_logic；timeout_ms 默认 15000。reuse=false 遇到已存在编辑器会拒绝，不隐式开第二个工程编辑器。

**调用示例**

```json
{
  "reuse": true,
  "display_mode": "visible",
  "timeout_ms": 30000
}
```

### 12.2 godot_editor_close 保护文档后关闭编辑器

**入口层级**：默认工具。

先核对 editor_epoch 与整组文档版本，在编辑器中二次校验，再按未保存策略退出并等待进程真实结束。

**执行位置**：S/E，任务。

**主要参数**

| 参数 | 要求 | 类型与范围 | 说明 |
| --- | --- | --- | --- |
| `editor_epoch` | 必填 | 字符串 | 预期编辑器启动代次 |
| `expected_document_revision` | 必填 | 字符串 | 整组打开文档的预期版本 |
| `timeout_ms` | 可选 | 整数；1～3600000 | 本次操作或等待期限，单位毫秒 |
| `unsaved` | 可选 | 字符串；reject / save / discard | 未保存内容处理策略 |

**核心结果**：任务成功结果含 closed、editor_pid、场景和脚本标签信息；本服务启动的进程还报告实际 exit_code。

**使用约束**：editor_epoch 与 expected_document_revision 来自最新 editor_status。unsaved 默认 reject，可显式 save/discard；未命名场景先给路径保存。任务 succeeded 才表示进程已退出，可用于后续外部原生扩展构建。

**调用示例**

```json
{
  "editor_epoch": "<editor_epoch>",
  "expected_document_revision": "<document_revision>",
  "unsaved": "reject",
  "timeout_ms": 15000
}
```

### 12.3 godot_editor_restart 重启并恢复保存的文档

**入口层级**：默认工具。

完成安全关闭、真实退出和新进程就绪，再恢复已保存场景/脚本标签及活动场景。

**执行位置**：S/E，任务。

**主要参数**

| 参数 | 要求 | 类型与范围 | 说明 |
| --- | --- | --- | --- |
| `editor_epoch` | 必填 | 字符串 | 预期编辑器启动代次 |
| `expected_document_revision` | 必填 | 字符串 | 整组打开文档的预期版本 |
| `display_mode` | 可选 | 字符串；headless_logic / visible | 进程显示模式 |
| `timeout_ms` | 可选 | 整数；1～3600000 | 本次操作或等待期限，单位毫秒 |
| `unsaved` | 可选 | 字符串；reject / save / discard | 未保存内容处理策略 |

**核心结果**：任务结果含 closed、started、script_documents；新 state 的 editor_epoch 改变。

**使用约束**：版本与未保存策略同 editor_close；display_mode 可选。恢复限于可保存的文档，不恢复被丢弃内容、旧引用或运行实例；不提供 GDExtension 热卸载。

**调用示例**

```json
{
  "editor_epoch": "<editor_epoch>",
  "expected_document_revision": "<document_revision>",
  "unsaved": "save",
  "display_mode": "visible",
  "timeout_ms": 30000
}
```


### 12.4 godot_extension_reload 重载并核验原生扩展

**入口层级**：默认工具。

对当前工程已加载的 `.gdextension` 调用 Godot 原生重载，等待原生类注册、Inspector、脚本编辑器、延后的脚本语言及帮助文档刷新完成，再验证类型与方法。外部编译由工程工具链执行。

**执行位置**：S/E，任务。

**主要参数**

| 参数 | 要求 | 类型与范围 | 说明 |
| --- | --- | --- | --- |
| `path` | 必填 | 字符串 | 规范的 `res://` 工程扩展声明路径 |
| `editor_epoch` | 必填 | 字符串 | 最新 editor_status 返回的编辑器代次 |
| `expected_document_revision` | 必填 | 字符串 | 最新 editor_status 返回的整组文档版本 |
| `expected_library_hash` | 可选 | 64 位小写十六进制字符串 | 编译完成后原始动态库的 SHA256 |
| `expected_classes` | 可选 | 对象，最多 256 项；每类最多 256 个方法名 | 类名到必需方法名数组的映射；必须属于目标扩展 |

**核心结果**：先返回 job_id；job_wait 成功结果含 reloaded、verified、native_status、requires_restart、library_path/hash、classes（父类及本类方法/属性/信号名）、added_classes、removed_classes、editor_pid/epoch、document_revision、reload_revision、runtime_updated=false。

**使用约束**：先停止游戏并保存场景、脚本及其他资源。扩展必须已启用 `reloadable=true` 且绑定支持重载；给现有编辑器首次开启时需重启一次。禁止在运行中替换扩展。原生类父类变化等不受支持的操作仍可能需要重启；失败不会自动重放或关闭编辑器。任务成功同时表示原生刷新完成、期望类/方法存在且没有新增错误；原生返回 OK 但注册丢失、脚本刷新报错或库在重载过程中改变时，任务明确失败。若有意将某个类的所有方法移除，在 expected_classes 中显式给该类空数组。重载复用原生实例重建和属性恢复，不提供 Undo，也不保留已停止游戏的状态。

macOS 编辑器以每代独立的同目录临时副本装载动态库，保留 `@loader_path` 依赖和原始库的资源定位；副本在关闭库或编辑器结束时清理。扩展自身应隐藏内部符号，避免不同代的弱符号共享初始化状态。

**调用示例**

```json
{
  "path": "res://addons/example/example.gdextension",
  "editor_epoch": "<editor_epoch>",
  "expected_document_revision": "<document_revision>",
  "expected_classes": {"ExampleNode": ["get_generation"]}
}
```

## 13 运行实例

### 13.1 godot_run_start 启动自有调试实例

**入口层级**：默认工具。

启动服务自有的隔离游戏进程，接入现有编辑器调试器，按精确 PID 等待 tree_ready。

**执行位置**：S/E/R，任务。

**主要参数**

| 参数 | 要求 | 类型与范围 | 说明 |
| --- | --- | --- | --- |
| `arguments` | 可选 | 数组；最多 64 项 | 目标方法参数或游戏用户参数，按入口解释 |
| `display_mode` | 可选 | 字符串；headless_logic / hidden_gpu / visible | 进程显示模式 |
| `rendering_driver` | 可选 | 字符串；metal / vulkan / opengl3 | Godot 渲染驱动选择 |
| `rendering_method` | 可选 | 字符串；mobile / forward_plus / gl_compatibility | Godot 渲染器选择 |
| `scene` | 可选 | 字符串 | main、current 或 res:// 场景路径 |
| `timeout_ms` | 可选 | 整数；1～3600000 | 本次操作或等待期限，单位毫秒 |

**核心结果**：任务结果含 run_id、session_id、pid、owned、scene、display_mode、state 和 session 信息。

**使用约束**：scene 默认 main，可为 current 或现有 res:// 场景；current 要求场景已保存。display_mode 默认 headless_logic，可为 hidden_gpu/visible；可指定 rendering_method/driver。arguments 最多 64 个字符串，作为游戏用户参数传递。当前 tree_ready 握手等待上限为 9 秒，不能仅靠增大 timeout_ms 扩大这一内部阶段期限。

**调用示例**

```json
{
  "scene": "res://scenes/Preview.tscn",
  "display_mode": "visible"
}
```

### 13.2 godot_run_list 列出运行和调试会话

**入口层级**：默认工具。

同时列出当前服务自有进程记录和编辑器中已完成握手的调试会话。

**执行位置**：S/E。

**主要参数**

| 参数 | 要求 | 类型与范围 | 说明 |
| --- | --- | --- | --- |
| `include_stopped` | 可选 | 布尔 | 包含本服务已退出运行记录 |

**核心结果**：runs、sessions；可据此关联 run_id、session_id、PID、显示模式和就绪阶段。

**使用约束**：include_stopped 默认 false。外部接入的 session 可以检查或输入，但不能因此获得自有 run 的进程控制权。

**调用示例**

```json
{
  "include_stopped": true
}
```

### 13.3 godot_run_stop 停止服务自有实例

**入口层级**：默认工具。

定向请求游戏正常退出，并等待真实退出；超过期限时对该自有进程回收。

**执行位置**：S/E/R，任务。

**主要参数**

| 参数 | 要求 | 类型与范围 | 说明 |
| --- | --- | --- | --- |
| `run_id` | 必填 | 字符串 | 当前服务自有运行身份 |
| `timeout_ms` | 可选 | 整数；1～3600000 | 本次操作或等待期限，单位毫秒 |

**核心结果**：任务结果是进程快照，含 run_id、pid、state=exited、exit_code，必要时包含 stop_method 与正常退出请求的错误。

**使用约束**：run_id 必须属于当前 sidecar。timeout_ms 默认 5000；存在有界 terminate/kill 回收。不提供任意 PID 或其他服务实例的停止入口。

**调用示例**

```json
{
  "run_id": "<run_id>",
  "timeout_ms": 5000
}
```

### 13.4 godot_run_wait 等待实例阶段或会话数量

**入口层级**：默认工具。

等待明确自有实例的就绪/退出阶段，或等待编辑器达到指定就绪会话数量。

**执行位置**：S/E。

**主要参数**

| 参数 | 要求 | 类型与范围 | 说明 |
| --- | --- | --- | --- |
| `run_id` | 条件 | 字符串 | 当前服务自有运行身份 |
| `stage` | 条件 | 字符串；connected / tree_ready / first_physics / first_render / exited | 需要观察到的运行阶段 |
| `minimum_sessions` | 可选 | 整数；1～16 | 至少需要的就绪会话数 |
| `timeout_ms` | 可选 | 整数；1～3600000 | 本次操作或等待期限，单位毫秒 |

**核心结果**：阶段模式返回 stage 与 session，退出模式返回 reached 和进程状态；数量模式返回 sessions。

**使用约束**：两种参数分支不能混用：run_id+stage，或 minimum_sessions。stage 为 connected/tree_ready/first_physics/first_render/exited。dummy 拒绝 first_render；退出等待到期可返回 reached=false。就绪阶段内部等待最多 9 秒；自有进程退出等待使用指定期限。数量等待不替代精确实例身份。

**调用示例**

```json
{
  "run_id": "<run_id>",
  "stage": "first_render",
  "timeout_ms": 5000
}
```


## 14 输入等待与界面截图

### 14.1 godot_input_send 向指定实例发送输入

**入口层级**：默认工具。

将类型化输入发送到明确运行会话，覆盖键盘、文本、动作、鼠标、触摸和手柄。

**执行位置**：E/R。

**主要参数**

| 参数 | 要求 | 类型与范围 | 说明 |
| --- | --- | --- | --- |
| `kind` | 必填 | 字符串；key / text / action_state / action_event / mouse_button / mouse_motion / wheel / touch / touch_motion / joy_button / joy_motion | 明确操作类型 |
| `session_id` | 必填 | 字符串 | 明确运行实例 |

**核心结果**：injected；输入具体效果由目标实例的 Input 状态或事件回调验证。

**使用约束**：session_id/kind 必填，事件字段按 kind 严格区分。按键持有归 MCP 连接所有。action_state 改状态，action_event 进入事件分发；二者不能互相替代。完整事件分支见本节事件表。

**调用示例**

```json
{
  "session_id": "<session_id>",
  "kind": "key",
  "keycode": 65,
  "pressed": true
}
```


**输入事件分支**

所有分支均需 session_id 和 kind。下表只列该事件自身的字段，不同分支字段不得混传。

| kind | 事件字段 | 语义 |
| --- | --- | --- |
| key | keycode / physical_keycode / unicode 至少一项非零；pressed、echo、修饰键可选 | 键盘事件 |
| text | text，最多 4096 字符 | 向当前引擎文本输入目标提交文本，不是 OS 输入法自动化 |
| action_state | action；pressed、strength 可选 | 修改 Input 动作状态，不冒充 Viewport 事件 |
| action_event | action；pressed、strength 可选 | 分发 InputEventAction |
| mouse_button | button；pressed、x/y、修饰键可选 | 按钮为 1/2/3/8/9 |
| mouse_motion | x/y、delta_x/delta_y、修饰键 | Viewport 像素位置及相对位移 |
| wheel | direction；factor、x/y、修饰键可选 | direction 为 up/down/left/right，自动处理瞬时滚轮事件 |
| touch | index；pressed、x/y 可选 | 多点触摸按下或释放 |
| touch_motion | index；x/y、delta_x/delta_y 可选 | 拖动明确触点 |
| joy_button | joy_button；device、pressed、strength 可选 | 手柄按钮，设备默认值按运行实现 |
| joy_motion | axis、value；device 可选 | 手柄轴，value 为 -1～1 |

修饰键为 shift、ctrl、alt、meta。strength 为 0～1；触点 index 为 0～31；device 为 0～15；joy_button 为 0～127；axis 为 0～9。输入注入后的游戏行为通过对象状态、UI、条件等待或截图验证。

### 14.2 godot_input_release_all 释放当前连接的输入

**入口层级**：默认工具。

释放该连接在指定实例上持有的按键、鼠标、触摸、动作和手柄状态。

**执行位置**：E/R。

**主要参数**

| 参数 | 要求 | 类型与范围 | 说明 |
| --- | --- | --- | --- |
| `session_id` | 必填 | 字符串 | 明确运行实例 |

**核心结果**：released，为回收的持有项数量。

**使用约束**：session_id 必填。不会释放其他连接仍持有的同一输入。断连也触发本连接输入清理，但正常流程应主动释放。

**调用示例**

```json
{
  "session_id": "<session_id>"
}
```

### 14.3 godot_input_sequence 执行帧间隔输入序列

**入口层级**：扩展工具，按需发现和调用。

按过程帧间隔发送一组类型化输入，在完成、超时或中断时释放该连接相关输入。

**执行位置**：E/R。

**主要参数**

| 参数 | 要求 | 类型与范围 | 说明 |
| --- | --- | --- | --- |
| `session_id` | 必填 | 字符串 | 明确运行实例 |
| `steps` | 必填 | 数组；最多 256 项 | 帧间隔与事件组成的步骤数组 |
| `timeout_ms` | 可选 | 整数；1～9000 | 本次操作或等待期限，单位毫秒 |

**核心结果**：completed、frames、released=true。

**使用约束**：steps 为 1～256 项，每项有 event，可选 after_frames 为 0～120。事件结构与 input_send 相同，但 event 内不重复 session_id。timeout_ms 上限 9000，默认 8000；同连接同一时刻只能有一个序列。

**调用示例**

```json
{
  "session_id": "<session_id>",
  "steps": [
    {
      "event": {
        "kind": "key",
        "keycode": 65,
        "pressed": true
      }
    },
    {
      "after_frames": 2,
      "event": {
        "kind": "key",
        "keycode": 65,
        "pressed": false
      }
    }
  ]
}
```

### 14.4 godot_runtime_wait 等待帧属性或信号条件

**入口层级**：默认工具。

使用实例的帧通知、属性轮询事件与信号，等待明确条件成立或目标失效。

**执行位置**：E/R。

**主要参数**

| 参数 | 要求 | 类型与范围 | 说明 |
| --- | --- | --- | --- |
| `session_id` | 必填 | 字符串 | 明确运行实例 |
| `condition` | 条件 | 字符串；frames / property / signal | 等待条件类型 |
| `timeout_ms` | 可选 | 整数；1～9000 | 本次操作或等待期限，单位毫秒 |

**核心结果**：condition、satisfied、frames、clock，以及过程/物理帧号、暂停状态；属性条件可含 actual。

**使用约束**：condition 默认 frames，clock 默认 process；frames 为 1～120。property 分支需目标、property/value，可选 eq/ne/lt/le/gt/ge；signal 分支需目标和 signal_name。非帧目标以 node_path/object_ref 二选一。timeout_ms 默认 5000、上限 9000；暂停物理、目标释放或断连明确失败，不使用硬睡眠。

**调用示例**

```json
{
  "session_id": "<session_id>",
  "condition": "frames",
  "clock": "physics",
  "frames": 2,
  "timeout_ms": 5000
}
```


**条件分支**

| condition | 必要信息 | 等待与提前失败 |
| --- | --- | --- |
| frames 或省略 | session_id；frames、clock 可选 | 过程或物理帧通知；暂停时物理等待返回 PAUSED |
| property | session_id、明确对象、property、value | 每次过程帧检查；排序比较只支持数字，eq/ne 可比较相应类型化值 |
| signal | session_id、明确对象、signal_name | 等信号发生；当前版本只报告发生，不返回信号参数列表 |

超时返回 TIMEOUT。普通对象释放或节点退出树返回 OBJECT_EXPIRED；调试实例断连由会话路由提前失败，不等待另一个实例的同名对象。

### 14.5 godot_ui_inspect 检查运行界面布局

**入口层级**：扩展工具，按需发现和调用。

遍历指定运行节点范围，读取 Control 的几何、可见性、焦点和可用文本。

**执行位置**：E/R。

**主要参数**

| 参数 | 要求 | 类型与范围 | 说明 |
| --- | --- | --- | --- |
| `node_path` | 必填 | 字符串 | 目标节点路径 |
| `session_id` | 必填 | 字符串 | 明确运行实例 |
| `max_nodes` | 可选 | 整数；1～4096 | UI 控件数量预算 |

**核心结果**：controls、truncated；控件项含 path、class、object、visible、focused、clip_contents、rect，适用控件另有 text/disabled。

**使用约束**：session_id/node_path 必填，max_nodes 默认 256、范围 1～4096。仅表示几何和控件状态，不是像素遮挡或图像识别结论。

**调用示例**

```json
{
  "session_id": "<session_id>",
  "node_path": "/root/Preview",
  "max_nodes": 256
}
```

### 14.6 godot_ui_focus 设置运行控件焦点

**入口层级**：扩展工具，按需发现和调用。

对明确会话中的可见、可聚焦 Control 请求引擎焦点。

**执行位置**：E/R。

**主要参数**

| 参数 | 要求 | 类型与范围 | 说明 |
| --- | --- | --- | --- |
| `node_path` | 必填 | 字符串 | 目标节点路径 |
| `session_id` | 必填 | 字符串 | 明确运行实例 |

**核心结果**：focused、path；以实际 has_focus 结果确认。

**使用约束**：目标必须可见且 focus_mode 非 FOCUS_NONE。只处理 Godot Control，不激活 OS 窗口；文本输入仍需 input_send(kind=text)。

**调用示例**

```json
{
  "session_id": "<session_id>",
  "node_path": "/root/Preview/NameEdit"
}
```

### 14.7 godot_capture_frame 捕获指定 GPU 画面

**入口层级**：默认工具。

捕获游戏主 Viewport、指定 Viewport 或当前 Camera 的真实渲染图像，并登记 PNG 产物。

**执行位置**：S/E/R。

**主要参数**

| 参数 | 要求 | 类型与范围 | 说明 |
| --- | --- | --- | --- |
| `session_id` | 必填 | 字符串 | 明确运行实例 |
| `height` | 可选 | 整数；1～2048 | 与 width 配合的输出图像高度 |
| `node_path` | 可选 | 字符串 | 目标节点路径 |
| `object_ref` | 可选 | 字符串 | 工具返回的对象句柄 |
| `target` | 可选 | 字符串；game / viewport / camera | game、viewport 或 camera |
| `width` | 可选 | 整数；1～2048 | 输出图像宽度 |

**核心结果**：原生 MCP image；结构化结果含 artifact_id、width/height、driver、renderer、target、viewport_path、帧信息等。

**使用约束**：session_id 必填，target 默认 game。viewport/camera 需指定目标；Camera 必须是其 Viewport 当前相机，不会临时替换相机。源图最多 4194304 像素，PNG 最多 3 MiB；width/height 可请求 1～2048 的输出尺寸，提供 height 时同时给 width。dummy 不提供画面，图像比较交给外部工具。

**调用示例**

```json
{
  "session_id": "<session_id>",
  "target": "game",
  "width": 1024,
  "height": 576
}
```


## 15 动画与骨骼姿态

### 15.1 godot_animation_list 列出动画资源

**入口层级**：扩展工具，按需发现和调用。

列出 AnimationPlayer、AnimationLibrary 或单个 Animation 中的动画及资源引用。

**执行位置**：E 或 E/R。

**主要参数**

| 参数 | 要求 | 类型与范围 | 说明 |
| --- | --- | --- | --- |
| `context` | 条件 | 字符串；editor / runtime | 编辑态或运行态，遵循公共目标规则 |
| `scene_id` | 条件 | 字符串 | 明确编辑场景 |
| `session_id` | 条件 | 字符串 | 明确运行实例 |
| `node_path` | 可选 | 字符串 | 目标节点路径 |
| `object_ref` | 可选 | 字符串 | 工具返回的对象句柄 |

**核心结果**：animations、truncated；每项含 name、length、loop_mode、tracks、animation。

**使用约束**：使用公共对象目标；最多列 256 项。单个 Animation 以空名称表示。不将 AnimationTree 当作任意动画库读取入口。

**调用示例**

```json
{
  "scene_id": "<scene_id>",
  "node_path": "AnimationPlayer"
}
```

### 15.2 godot_animation_inspect 检查轨迹和关键帧

**入口层级**：扩展工具，按需发现和调用。

读取指定 Animation 的轨迹目标、类型、插值、关键帧时间和值。

**执行位置**：E 或 E/R。

**主要参数**

| 参数 | 要求 | 类型与范围 | 说明 |
| --- | --- | --- | --- |
| `context` | 条件 | 字符串；editor / runtime | 编辑态或运行态，遵循公共目标规则 |
| `scene_id` | 条件 | 字符串 | 明确编辑场景 |
| `session_id` | 条件 | 字符串 | 明确运行实例 |
| `animation_name` | 可选 | 字符串 | 目标动画名称 |
| `max_keys` | 可选 | 整数；1～4096 | 跨轨迹共享的关键帧或样本预算 |
| `node_path` | 可选 | 字符串 | 目标节点路径 |
| `object_ref` | 可选 | 字符串 | 工具返回的对象句柄 |

**核心结果**：length、loop_mode、tracks、truncated；轨迹含 index/type/path/enabled/interpolation/keys/key_count。

**使用约束**：目标可为 Animation 或可解析指定 animation_name 的 AnimationPlayer/Library。max_keys 默认 1024、上限 4096，所有轨迹共享预算，最多 256 轨迹。

**调用示例**

```json
{
  "scene_id": "<scene_id>",
  "node_path": "AnimationPlayer",
  "animation_name": "walk",
  "max_keys": 1024
}
```

### 15.3 godot_animation_edit 编辑自有动画资源

**入口层级**：扩展工具，按需发现和调用。

在副本上预检动画变更，再把轨迹或元数据改动提交为一次场景 Undo。

**执行位置**：E。

**主要参数**

| 参数 | 要求 | 类型与范围 | 说明 |
| --- | --- | --- | --- |
| `expected_revision` | 必填 | 整数；0～无上限 | 当前目标的预期版本 |
| `operation` | 必填 | 字符串；add_track / remove_track / set_key / remove_key / set_metadata | 动画或编辑操作分支 |
| `scene_id` | 必填 | 字符串 | 明确编辑场景 |
| `animation_name` | 可选 | 字符串 | 目标动画名称 |
| `node_path` | 可选 | 字符串 | 目标节点路径 |
| `object_ref` | 可选 | 字符串 | 工具返回的对象句柄 |

**核心结果**：applied、undoable=true、saved=false、track、revision。

**使用约束**：scene_id/expected_revision/operation 必填。operation 为 add_track/remove_track/set_key/remove_key/set_metadata，分支字段见下表。导入动画先复制为自有资源；修改外部动画仍需单独资源保存。

**调用示例**

```json
{
  "scene_id": "<scene_id>",
  "expected_revision": 7,
  "node_path": "AnimationPlayer",
  "animation_name": "walk",
  "operation": "set_key",
  "track": 0,
  "time": 0.5,
  "value": {
    "_type": "Vector3",
    "x": 0,
    "y": 1,
    "z": 0
  }
}
```


**动画编辑分支**

| operation | 分支必填字段 | 作用 |
| --- | --- | --- |
| add_track | track_type、path | 新增轨迹并设置 NodePath |
| remove_track | track | 删除轨迹 |
| set_key | track、time、value | 插入或设置指定时间的关键帧，按轨迹类型检查值 |
| remove_key | track、key | 按索引删除关键帧 |
| set_metadata | 可给 length、loop_mode | 更新动画时长或循环模式 |

各分支共同提供 scene_id、expected_revision，并以 node_path/object_ref 定位动画或播放器；通过播放器或动画库定位时给 animation_name。length 大于等于 0.001，loop_mode 为 0/1/2；轨迹与关键帧索引按零起算。

### 15.4 godot_animation_sample 采样轨迹值或骨骼姿态

**入口层级**：扩展工具，按需发现和调用。

提供轨迹插值、隔离单 clip 采样，以及实时已应用姿态读取三种用途。

**执行位置**：E 或 E/R。

**主要参数**

| 参数 | 要求 | 类型与范围 | 说明 |
| --- | --- | --- | --- |
| `mode` | 必填 | 字符串；tracks / isolated / live | tracks、isolated 或 live |
| `animation_name` | 条件 | 字符串 | 目标动画名称 |
| `context` | 条件 | 字符串；editor / runtime | 编辑态或运行态，遵循公共目标规则 |
| `scene_id` | 条件 | 字符串 | 明确编辑场景 |
| `session_id` | 条件 | 字符串 | 明确运行实例 |
| `times` | 条件 | 数组；最多 256 项 | 采样时刻数组，单位秒 |
| `max_bones` | 可选 | 整数；1～256 | 单次姿态快照共享的骨骼预算 |
| `max_keys` | 可选 | 整数；1～4096 | 跨轨迹共享的关键帧或样本预算 |
| `node_path` | 可选 | 字符串 | 目标节点路径 |
| `object_ref` | 可选 | 字符串 | 工具返回的对象句柄 |
| `properties` | 可选 | 数组；最多 64 项 | 直接节点属性选择器数组，含 node_path/property |
| `skeleton_paths` | 可选 | 数组；最多 16 项 | 相对动画根的 Skeleton3D 路径 |

**核心结果**：tracks 模式返回轨迹 samples；isolated/live 返回 samples、sampling_stage、playback_modified、truncated 等，isolated 另有 ignored_tracks、world_isolated。

**使用约束**：mode 必填。tracks/isolated 需要 times；isolated 还需 animation_name，目标必须是 AnimationPlayer。live 读取 AnimationPlayer/AnimationTree 当前状态，不接受 times，不 seek。skeleton_paths 相对 AnimationMixer.root_node 指向的动画根，properties 为直接节点属性选择器。三种模式的实际阶段与限制见下表。

**调用示例**

```json
{
  "scene_id": "<scene_id>",
  "node_path": "AnimationPlayer",
  "mode": "isolated",
  "animation_name": "walk",
  "times": [
    0,
    0.5,
    1
  ],
  "skeleton_paths": [
    "Rig"
  ],
  "max_bones": 128
}
```


**三种采样模式**

| mode | 输入与目标 | 实际采样阶段与副作用 |
| --- | --- | --- |
| tracks | Animation 或 Player/Library 中的动画；times 必填，最多 256 个时刻 | 返回轨迹插值，不应用到节点；max_keys 是共享样本预算，不能称为最终骨骼姿态 |
| isolated | AnimationPlayer；animation_name 与 times 必填，最多 16 个时刻 | 原生节点副本、隔离 World、手动单 clip seek；不复制项目脚本，跳过方法/音频/嵌套播放及共享资源目标轨迹；阶段为 after_single_clip_seek_before_modifiers |
| live | AnimationPlayer 或 AnimationTree；不接收 times/animation_name | 读取当前已应用骨骼和直接属性，不 seek、不改播放；阶段为 current_scene_state |

姿态查询最多遍历 4096 个原生节点；skeleton_paths 最多显式选择 16 个骨架，max_bones 为所有选中骨架共享的单次快照预算，默认 128、上限 256；properties 最多 64 项，每项为 node_path/property，只允许直接属性。骨骼数据给出 position、rotation、scale、local_pose、global_pose，坐标空间为骨架空间，不能直接解释为世界坐标。

isolated 不运行骨架 modifiers，不是完整 AnimationTree 混合或物理离线仿真，也不保证任意原生扩展构造函数无副作用。live 可观察当前实例实际已应用状态，但读取本身不驱动游戏完成某一混合或 IK 阶段。


## 16 调试与性能

### 16.1 godot_debugger_breakpoint_set 设置脚本断点

**入口层级**：扩展工具，按需发现和调用。

为工程脚本登记断点，作用于当前及后续编辑器调试会话。

**执行位置**：E，通过现有调试器。

**主要参数**

| 参数 | 要求 | 类型与范围 | 说明 |
| --- | --- | --- | --- |
| `line` | 必填 | 整数；1～无上限 | 脚本一基行号 |
| `path` | 必填 | 字符串 | 工程内资源或脚本路径 |

**核心结果**：breakpoint_id、registered、scope。

**使用约束**：path 是工程脚本，line 为一基行号。作用域为编辑器会话，不接受单实例 session_id 或条件断点表达式。实际是否命中依赖所运行语言与代码路径。

**调用示例**

```json
{
  "path": "res://scripts/probe.gd",
  "line": 12
}
```

### 16.2 godot_debugger_breakpoint_clear 清除脚本断点

**入口层级**：扩展工具，按需发现和调用。

按脚本路径及行号清除精确断点登记。

**执行位置**：E，通过现有调试器。

**主要参数**

| 参数 | 要求 | 类型与范围 | 说明 |
| --- | --- | --- | --- |
| `line` | 必填 | 整数；1～无上限 | 脚本一基行号 |
| `path` | 必填 | 字符串 | 工程内资源或脚本路径 |

**核心结果**：breakpoint_id、registered、scope。

**使用约束**：使用与设置相同的 path 和一基 line。registered 描述清除后的登记状态，不表示目标游戏已经退出暂停。

**调用示例**

```json
{
  "path": "res://scripts/probe.gd",
  "line": 12
}
```

### 16.3 godot_debugger_state 读取暂停栈和变量

**入口层级**：默认工具。

按明确运行会话读取调试状态；暂停时可请求指定帧变量，不依赖游戏继续执行帧。

**执行位置**：E，通过现有调试器。

**主要参数**

| 参数 | 要求 | 类型与范围 | 说明 |
| --- | --- | --- | --- |
| `session_id` | 必填 | 字符串 | 明确运行实例 |
| `frame` | 可选 | 整数；0～255 | 暂停栈帧索引 |
| `max_variables` | 可选 | 整数；1～2048 | 暂停变量返回预算 |
| `timeout_ms` | 可选 | 整数；1～9000 | 本次操作或等待期限，单位毫秒 |
| `wait_paused` | 可选 | 布尔 | 等待目标进入暂停 |

**核心结果**：session_id、active、paused、reason、stack、variables、variables_truncated、revision。

**使用约束**：wait_paused 可等待暂停；frame 为 0～255，max_variables 默认 128、上限 2048，timeout_ms 默认 5000、上限 9000。未暂停时栈和变量不作为有效暂停快照。GDScript 已验证，其他语言受 Godot 原生调试能力限制。

**调用示例**

```json
{
  "session_id": "<session_id>",
  "frame": 0,
  "max_variables": 128,
  "wait_paused": true,
  "timeout_ms": 5000
}
```

### 16.4 godot_debugger_control 暂停继续或单步

**入口层级**：默认工具。

向明确调试会话发送控制指令，并等待所观察到的调试状态变化。

**执行位置**：E，通过现有调试器。

**主要参数**

| 参数 | 要求 | 类型与范围 | 说明 |
| --- | --- | --- | --- |
| `action` | 必填 | 字符串；pause / continue / step_into / step_over / step_out | 操作分支或 InputMap 动作，按入口解释 |
| `session_id` | 必填 | 字符串 | 明确运行实例 |
| `timeout_ms` | 可选 | 整数；1～9000 | 本次操作或等待期限，单位毫秒 |

**核心结果**：与 debugger_state 相同的目标状态快照。

**使用约束**：action 为 pause/continue/step_into/step_over/step_out；timeout_ms 上限 9000。步骤通过现有调试器执行，不是确定性游戏仿真或任意线程调度。

**调用示例**

```json
{
  "session_id": "<session_id>",
  "action": "step_over",
  "timeout_ms": 5000
}
```

### 16.5 godot_monitors_read 读取引擎监控指标

**入口层级**：扩展工具，按需发现和调用。

读取指定实例的原生 Performance 指标及单位和可用性。

**执行位置**：E/R。

**主要参数**

| 参数 | 要求 | 类型与范围 | 说明 |
| --- | --- | --- | --- |
| `session_id` | 必填 | 字符串 | 明确运行实例 |
| `metrics` | 可选 | 数组；最多 64 项 | Performance 原生常量名称数组 |

**核心结果**：metrics，每项含 name、value、unit、available；另有 process_frame、physics_frame、driver。

**使用约束**：metrics 可为 1～64 个 Performance 常量名称；默认读取 FPS、过程/物理耗时、静态内存与节点数。dummy 中不可用的绘制指标标记 unavailable，不用 0 冒充测量值；不提供任意自定义指标表达式。

**调用示例**

```json
{
  "session_id": "<session_id>",
  "metrics": [
    "TIME_FPS",
    "MEMORY_STATIC",
    "OBJECT_NODE_COUNT"
  ]
}
```

### 16.6 godot_profiler_capture 采集原生性能帧

**入口层级**：扩展工具，按需发现和调用。

按需开启现有 servers 或 visual profiler，采集有界帧后关闭，不进行后台常驻采样。

**执行位置**：E，通过现有调试器。

**主要参数**

| 参数 | 要求 | 类型与范围 | 说明 |
| --- | --- | --- | --- |
| `session_id` | 必填 | 字符串 | 明确运行实例 |
| `frames` | 可选 | 整数；1～120 | 帧数预算或等待帧数 |
| `kind` | 可选 | 字符串；servers / visual | 明确操作类型 |
| `timeout_ms` | 可选 | 整数；1～9000 | 本次操作或等待期限，单位毫秒 |

**核心结果**：session_id、kind、frames、signatures、complete、timed_out、source=engine_profiler；帧记录包含该 profiler 可提供的数据。

**使用约束**：kind 默认 servers，frames 默认 8、范围 1～120，timeout_ms 默认 5000、上限 9000。要求未暂停的活动实例；同实例仅一项采集。visual 需要 GPU，CPU 与 GPU 字段按原生含义解释。complete=false 表示未收满目标帧数，即使已经返回部分数据。

**调用示例**

```json
{
  "session_id": "<session_id>",
  "kind": "servers",
  "frames": 8,
  "timeout_ms": 5000
}
```


## 17 常用工具组合

### 17.1 文件优先的场景修改

日常已保存场景由文件工具编辑。目标在编辑器中打开时，先通过 scene_list 检查 dirty；有未保存内容先处理冲突，不能直接覆盖。需要关闭或重新打开文档时，通过发现入口获取 scene_close/open 契约。写盘后按需要扫描或导入，再运行验证。

只有修改编辑器未保存场景、需要保留 Undo 或资源只存在于内存时，才使用 edit_batch/object_set_properties/resource 工具。多项结构操作合并为一个 edit_batch；使用最新 revision，最后明确保存。不要把直接文件修改描述为已经加入编辑器 Undo。

### 17.2 外部代码修改后更新编辑器

外部代码工具修改脚本前，检查未保存缓冲区。未保存内容不能自动覆盖；已经打开的脚本文档与磁盘冲突需先处理。写盘后按需 filesystem_scan，资源需要时才 resource_reimport，关闭的缓存脚本需要时才 script_reload；不在每次编辑后无条件执行所有刷新。

运行实例需要更新时，用受控 run_stop/start。script_read/patch 仅用于必须处理 CodeEdit 当前内存内容的情况，不承担普通磁盘代码编辑。

### 17.3 运行并检查界面

run_start → job_wait 到 succeeded → 从 result 取 run_id/session_id → run_wait 等 tree_ready 或 first_render → ui_inspect/ui_focus/input_send 或 input_sequence → runtime_wait 等明确状态 → capture_frame → 外部工具分析图像 → run_stop 并等待真实退出。整个过程保持同一个 sidecar 服务。

### 17.4 原生扩展更新

editor_status 取得 editor_epoch/document_revision → editor_close 并等任务 succeeded → 由外部构建工具编译安装扩展 → editor_start → 核对新代次和 class_describe → 启动游戏验证。没有运行时热卸载承诺，构建与服务部署由外部工具负责。

### 17.5 动画与暂停调试

animation_list/inspect 检查来源 → 需要修改导入动画时先 resource_duplicate → animation_edit 与保存 → animation_sample 根据问题选择 tracks/isolated/live。调试代码使用 breakpoint_set → 运行并等待 debugger_state.paused → debugger_control 单步或继续。暂停调试与实时采样的执行条件不同，不依赖已暂停的游戏继续处理普通 Runtime Helper 请求。

## 18 预算与错误处理

### 18.1 资源预算

| 资源 | 当前上限或期限 |
| --- | --- |
| JSON | 输入 4 MiB、结果 8 MiB、深度 32、单数组 4096 项、总节点 100000 |
| 类型化值 | 嵌套解码深度 12；VariantText 输入文本最多 65536 字符 |
| 对象引用 | 每个编辑器或运行上下文 1025 项；120 秒访问续租；显式 release 不销毁 Node |
| 树快照 | 每上下文缓存 8 个，30 秒有效；最多 16384 节点和 2 MiB 元信息；单页最多 1025 项 |
| 结构编辑 | 每批最多 256 操作，批量预检最多 4096 个场景节点 |
| 脚本缓冲区 | 1 MiB，最多 256 个精确替换 |
| 任务 | 服务最多保留 128 条记录、8 个活动操作；已完成任务不自动变成无限历史 |
| 自有运行记录 | 服务最多 64 条，查询过滤已停止实例不等于删除记录 |
| Output 文本 | 每页最多 65536 Unicode 字符；面板缓冲区读取预算 1048576 字符；后续页校验代次与版本 |
| 事件与诊断 | 各保留最近 2048 项；单条日志正文最多 16384 字符 |
| 产物 | 512 个登记产物；单个最多 32 MiB；单次读取最多 1 MiB；服务生命周期内有效 |
| 图像 | 源画面最多 4194304 像素；PNG 最多 3 MiB；可请求输出尺寸 1～2048 |
| 性能帧 | 最多 120 帧、9 秒；每实例同一时刻一项采集 |
| 输入序列 | 最多 256 步，每步间隔最多 120 过程帧，总期限最多 9 秒 |

树结果需同时检查 has_more 和 source_truncated。前者表示还有已缓存页，后者表示源遍历已被预算截断；两者不能混同。运行快照可能含 expired 节点，不能假定引用仍存活。

### 18.2 常见错误和恢复

| 错误 | 含义和处理 |
| --- | --- |
| INVALID_ARGUMENT / INVALID_VALUE | 字段分支、范围或 Godot 值类型不匹配；核对该项 Schema，不把任意字段混入其他模式 |
| REVISION_CONFLICT / DOCUMENT_CONFLICT / EDITOR_CONFLICT | 场景、缓冲区、整组文档或编辑器代次已改变；重新读取后再决定是否重做操作 |
| DISK_CONFLICT | 当前磁盘内容不匹配预期哈希；检查外部变更，不能直接用 overwrite 绕过 |
| UNSAVED_CHANGES / PATH_REQUIRED | 未保存内容未被明确处理，或新文档缺少路径；先保存或明确选择策略 |
| DOCUMENT_INACTIVE | 操作要求活动文档；先 scene_open 激活相同目标 |
| OBJECT_NOT_FOUND / OBJECT_EXPIRED / DOCUMENT_NOT_OPEN | 对象或缓冲区已失效；重新获取实际目标，不能猜测新句柄 |
| SNAPSHOT_CHANGED / SNAPSHOT_EXPIRED / SNAPSHOT_CONFLICT | 快照版本、租期或查询目标不匹配；从新快照第一页重新查询 |
| OUTPUT_CHANGED | 面板文本或筛选在分页期间改变，重新读取第一页；旧编辑器代次返回 EDITOR_CONFLICT |
| REFERENCE_BUDGET / RESULT_TOO_LARGE | 引用或结果超过预算；主动释放、缩小查询或分页 |
| TIMEOUT / PAUSED | 条件未在期限内完成，或物理模拟暂停；按实际状态处理，不将超时当作撤销 |
| SESSION_NOT_FOUND / NO_ACTIVE_SESSION | 明确目标会话已断连或不存在；重新列出实例，不改投另一个实例 |
| UNSUPPORTED_DISPLAY / CAPTURE_UNAVAILABLE | 当前显示模式无法提供所需 GPU 结果；选择受支持的图形模式并核实驱动 |
| RESULT_CONTRACT_VIOLATION / TRANSPORT_ERROR | 结果无法校验或传输失败，应用结果可能不确定；先读取状态，禁止自动重放修改 |

## 19 版本维护与参考资料

新增或修改公开工具时，同步更新注册表、输入输出 Schema、本手册、示例及部署副本。手册中的全部 63 个示例通过当前注册表的参数 Schema 校验；该校验只验证调用结构，不替代项目中的实际对象、脚本方法或资源存在性检查。

- [机器可读工具目录](tool_catalog.json)：公开输入与成功输出契约。
- [安装和使用指南](../README_zh-CN.md)：工程配置、常驻服务和复测方式。
- [职责边界](tool_scope.md)：MCP 会话能力与外部无头工具的分工。
- [编辑器一致性约束](editor_consistency.md)：原生命令、人工操作共存、资源引用及执行边界。
- [引擎补丁维护](engine_patches.md)：使用既有 API 的薄补丁、构建和升级要求。

本手册随源码和部署副本维护；使用前应通过 editor_status 核对实际引擎接口与插件版本。
