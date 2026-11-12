# MCP 与外部工具的职责

MCP 面向当前 Godot 编辑器和已连接调试实例。日常磁盘源码、已保存场景、资源和配置优先直接修改文件；需要未保存文档、Undo、对象身份、暂停状态或运行实例时使用 MCP。

## 工具分工

| 工作 | 执行方式 |
| --- | --- |
| 当前场景／资源编辑、脚本缓冲区、Undo | MCP 文档、属性、批量编辑和 CodeEdit 工具 |
| 编辑器启动／关闭／重启、扫描和指定重导入 | MCP 会话与文件系统工具 |
| 定向运行、多实例、输入、UI、条件等待与 GPU 截图 | MCP 显式 run_id/session_id 工作流 |
| 动画／骨骼检查、断点、单步、栈变量和性能帧 | MCP 当前资源／调试会话工具 |
| 磁盘文件创建、编辑、搜索和静态依赖分析 | 文件工具、rg、工程脚本或无头 Godot |
| 独立脚本、测试、碰撞／导航资源烘焙、导出 | 原生无头 CLI 与工程工具链 |
| 截图解释、图像比较、报告生成 | 消费 MCP 截图产物的外部工具 |

插件保持通用，不包含工程业务专用接口，也不提供任意命令、脚本执行或另一套构建／测试平台。默认展示高频工具，扩展能力通过 tools_discover/tools_call 按需使用，完整契约见 [工具手册](tool_reference_zh-CN.md)。

## 外部命令组合

GODOT 和 PROJECT 来自使用者的环境或本地配置。引擎路径通过使用者的本地配置或显式参数指定；受控文档与配置不得写死本机路径。下例中的脚本、测试框架与 preset 由实际工程提供。

```bash
# 工程开发脚本或业务烘焙
"$GODOT" --headless --path "$PROJECT" --script res://tools/bake.gd

# 检查磁盘脚本，不执行脚本入口
"$GODOT" --headless --path "$PROJECT" --check-only --script res://scripts/example.gd

# 工程使用 GUT 时调用其命令行
"$GODOT" --headless --path "$PROJECT" --script res://addons/gut/gut_cmdln.gd -- -gtest=res://tests/test_example.gd -gexit

# 使用工程配置的导出 preset
"$GODOT" --headless --path "$PROJECT" --export-pack "Preset Name" build/project.pck
```

编辑闭环：外部工具修改文件 → 按需扫描／导入 → MCP 运行、观测或调试 → 外部工具分析产物。烘焙闭环由工程自己的无头命令及验证步骤完成，不依赖编辑器会话。
