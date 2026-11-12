# Output 面板操作

适用 Godot MCP 0.6.1，协议 2。公开工具为 `godot_output_control`，位于默认目录；读取不受面板显示影响的结构化诊断仍使用 `godot_diagnostics_read`。

## 操作范围

| action | 用途 | 主要参数 |
| --- | --- | --- |
| get 或省略 | 查询面板可见状态、级别、搜索、折叠、计数、文本版本及诊断保留状态 | 无其他参数 |
| configure | 配置真实 Output dock | visible、levels、search、collapse 中至少一项 |
| read | 读取经过筛选与折叠后的面板纯文本 | offset、max_chars；后续页需要 expected_revision 和 editor_epoch |
| clear | 清空面板、指定截止之前的诊断或二者 | editor_epoch；scope 默认 panel，diagnostics/all 还须 through_cursor |

这些操作作用于当前工程编辑器的同一个 Output 面板，所有连接共享该状态。按运行实例筛选诊断使用 diagnostics_read(session_id)，不把面板筛选伪装成实例隔离。

## 查看与显示配置

查询状态：

```json
{"action":"get"}
```

返回 visible、levels、search、collapse、counts、message_count、stored_rows、line_limit、text_characters、revision、editor_epoch，以及 diagnostics 中的 latest_cursor、oldest_cursor、retained_count、history_generation、cleared_through_cursor。

显示面板并仅看错误与警告：

```json
{"action":"configure","visible":true,"levels":["error","warning"],"search":"","collapse":true}
```

levels 可选 info/error/warning/editor，富文本标准输出归入 info。传空数组隐藏所有级别；省略某个配置项保持原值。search 最多 1024 字符，使用面板原生不区分大小写的子串搜索。collapse 使用原生连续重复消息折叠。visible 控制 dock 显示或收起，不是 OS 焦点自动化；状态表示控件树可见性。

计数是面板收到的各类消息数量，搜索、级别和折叠不会重置计数。级别与折叠沿用 Godot 的设置保存逻辑。清空不会重置这些显示配置。

## 读取面板文本

```json
{"action":"read","max_chars":4096}
```

返回 text、next_offset、total_characters、has_more、revision 和 editor_epoch。文本是面板当前缓冲区的纯文本，遵循级别、搜索、重复折叠与最大行数；BBCode 标签已解析，不是原始诊断记录，也不局限于当前滚动位置能看到的像素区域。

下一页使用同一编辑器代次和文本版本：

```json
{"action":"read","offset":4096,"max_chars":4096,"expected_revision":123,"editor_epoch":"<editor_epoch>"}
```

offset 与长度均按 Unicode 字符计；max_chars 默认 16384，上限 65536。面板缓冲区超过 1048576 字符时，先缩小筛选范围。新消息或筛选改变导致 OUTPUT_CHANGED，编辑器重启导致 EDITOR_CONFLICT，均需重新读取第一页。持续日志流应使用 diagnostics_read 的增量游标。

## 清空面板与清空诊断

### 清空面板

```json
{"action":"clear","scope":"panel","editor_epoch":"<editor_epoch>"}
```

效果与面板 Clear 按钮一致：清空文字、存储行、消息计数和 Output 警告/错误图标。**诊断历史不删除，游标和 history_generation 不改变。** 这是 clear 的默认范围，不允许附带 through_cursor，以免把清面板误认为清诊断。

### 按截止游标清空诊断

先从 get 的 diagnostics.latest_cursor 或 diagnostics_read.latest_cursor 取得截止值，再明确提交：

```json
{"action":"clear","scope":"diagnostics","through_cursor":1234,"editor_epoch":"<editor_epoch>"}
```

只删除当前仍保留且 cursor ≤ 1234 的诊断。读取截止值之后新产生的消息全部保留；面板文字和计数保持原样。游标从不归零，后续新记录继续递增。history_generation 增加，cleared_through_cursor 标记主动清空边界，旧读取游标落在已删区间时 dropped=true。

因此清空完成后 retained_count 可以大于零，这表示截止值之后已有新日志，不是清空失败。

### 同时清空

```json
{"action":"clear","scope":"all","through_cursor":1234,"editor_epoch":"<editor_epoch>"}
```

清空整个面板，同时仅删除截止值之前的诊断，保留更新的诊断。结果有 cleared_panel、cleared_diagnostics、removed_diagnostics 以及更新后的状态。through_cursor 不能大于当前 latest_cursor；编辑器代次必须匹配，整个请求通过校验后才应用。

## 与其他错误记录的区别

这里的计数、清空和显示针对 Output dock 及 MCP 诊断环。Debugger 面板的独立错误树、脚本解析器内部状态、断点以及启动日志文件不随此操作删除。清空记录也不表示源代码错误已经修复；修复后应检查新产生的诊断或重新验证。

## 与人工操作共用处理层

configure 在完整校验后，调用真实按钮的 toggled／LineEdit 的 text_changed 路径；对应原生 handler 维护 active/collapse、日志重建、计数与状态保存。显示／收起走 EditorDock；清面板使用 Clear 按钮所用的 _clear_request。没有另一份 MCP 面板或只改控件外观的快捷路径。

Output 配置与清面板按原生行为不进入场景 Undo，不能因此把场景标为脏。结构化诊断历史属于 MCP 增量日志，独立用 cutoff／epoch 管理；不等于 Debugger Errors 的清空。

人工与 MCP 共享同一个面板状态；一个来路的修改会被另一个来路的查询和操作直接观察到。原生处理路径见 [编辑器一致性约束](editor_consistency.md)。
