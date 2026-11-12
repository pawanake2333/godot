# 编辑器一致性约束

适用插件 0.6.1 / 协议 2。具体工具参数见 [工具参考手册](tool_reference_zh-CN.md)。

## 原生编辑语义

MCP 文档修改必须进入 Godot 的 Inspector、编辑器命令或 UI handler，使真实文档对象、资源引用、关联属性、操作栈、脏状态、插件钩子和 UI 通知共同生效。批量编辑复用同一套原生记录与完成处理。只读查询不触发编辑；Output 等原生不进入场景 Undo 的操作也不写入场景历史。

| 操作 | 处理路径 |
| --- | --- |
| 属性写入 | EditorInterface.edit_inspector_properties → EditorInspector.commit_property_changes；与手工 _property_changed/_edit_set 共用关联属性、Control 布局状态、资源引用及插件 Undo 钩子 |
| 编辑完成 | 原生 _property_edit_completed 负责刷新和 property_edited；重启提示共用 restart_requested。Inspector 已换对象时，旧 Undo 不向新目标发送编辑通知 |
| 批量编辑 | 一个 EditorUndoRedoManager 事务；属性使用原生 property operation，结构引用更新和删除清理复用 SceneTreeDock |
| 脚本缓冲 | 当前 CodeEdit 的复合编辑与 Undo/Redo；不通过写 Script.source_code 绕过编辑器缓冲 |
| 保存与退出 | EditorInterface → EditorNode 原生文档／资源保存、缓存维护、保存通知、布局保存与插件清理 |
| 断点与性能采样 | 使用原生 ScriptEditor 断点表；已有人工 profiler 仅观察，自己开启的采样只有配置代次未变化时才关闭 |
| Output | 显示走 EditorDock；筛选、折叠和搜索走真实控件信号与原生 handler；清面板使用 _clear_request，详见 [Output 指南](output_control.md) |

## 身份、版本与历史

- scene_id、对象句柄和缓冲区句柄绑定编辑器会话；关闭、释放或重启后不能改投其他对象。
- scene_list.revision 为会话内单调历史序号。任意场景／全局历史提交、Undo、Redo 都使旧序号失效；撤销回原值不会让旧请求重新有效。
- 原生历史管理器负责栈顺序、保存版本和版本通知。活动场景的 Undo/Redo 按原生菜单规则选择场景／全局／远程历史；非活动场景只操作其指定历史。
- 普通节点属性支持明确指定的非活动文档；结构与动画编辑要求活动文档。属性事务不强制切换人工的 Inspector 选择。
- 场景、脚本与其他资源的未保存内容必须显式处理。没有通用版本契约的其他插件脏缓冲会阻止关闭，须先在所属编辑器保存。

## 对象、资源与批量编辑

- 编辑器基础设施只读。内部／外来节点、场景路径、资源路径、脚本和 Shader 源码不能借通用属性或方法接口修改；使用所属文档入口。
- 属性值和持久信号绑定不能保存编辑器对象或其他场景节点。
- 导入资源与外来场景内嵌资源遵守原生只读规则。无路径资源只有来自文档存储属性，或显式创建／复制后才能修改。
- 共享 Resource 影响所有使用者，按原生规则进入场景或全局历史。保存不能接管其他缓存对象的路径；已命名资源改路径须先复制。场景另存不能覆盖其他已打开文档。
- 改名／移动复用原生 NodePath 和动画引用更新；动画轨道自动改名遵守编辑器偏好。
- rename/reparent/delete 不与引用／容器属性赋值、attach 混在同一批；有歧义的组合在实例化前拒绝，须拆成明确步骤。
- 编辑态 object_call 仅允许公开 const 方法；两项动画库添加操作有类型校验和原生逆操作。运行态非 const 调用须显式声明 allow_mutation。
- 项目设置遵循原生 General Inspector 的可编辑标志。InputMap、插件、autoload、导入等专用模型的设置交给对应原生编辑器或离线文件流程。

## 执行边界

运行态修改是临时调试行为，不自动保存到场景。完整结构／动画 Live Edit 尚不支持：存在活动游戏会话时拒绝这两类编辑；先停止运行再编辑，或使用明确的 runtime 工具并重新运行验证。

@tool 自定义 setter、资源脚本、GDExtension 与其他插件可执行任意项目代码，其副作用遵循 Godot 原生边界。dry_run 检查编辑规划，不提供任意用户代码的事务回滚。超时或断线后先检查状态，不能自动重放修改。
