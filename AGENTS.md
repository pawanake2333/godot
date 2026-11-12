# 定制 Godot 工作区

本仓库是用户 Godot fork 的 4.7.2-stable-mcp 分支。Godot 引擎定制与通用 MCP 的唯一开发源位于当前仓库；业务工程中的 addons/godot_mcp 是部署副本，不在副本上单独开发。

- 不改变已有文件暂存/未暂存状态，不覆盖无关修改；没有用户要求不提交或推送。
- 日常修改引擎源码及 addons/godot_mcp；实现变更同步更新相关文档、脚本与契约。
- 引擎修改保持局部，优先薄绑定已有 API/信号。插件是通用开发辅助，不增加业务专用接口或原生自动化服务器。
- MCP 编辑态修改必须复用对应 Inspector／编辑器命令／UI handler，使原生文档、引用、Undo、脏状态和通知共同生效；不另造底层赋值加补通知的并行实现。Output 复用原生控件 handler，遵守其不进入场景 Undo 的行为。
- 无头测试、烘焙、导出、磁盘源码编辑与图像比较交给外部工具，范围见 addons/godot_mcp/docs/tool_scope.md。
- 第一方插件、脚本和测试禁止固定硬睡眠；等待 fd、条件变量、进程状态、信号、帧或实际 timer deadline。
- 不在受控代码、配置或文档写死本机绝对路径；外部路径通过显式参数、环境或 Git 忽略的本地配置提供，临时目录由系统提供。
- 默认 E2E 隐藏窗口；用户已明确允许当前插件开发正常显示窗口，本轮可用 --visible，其他任务不自动继承此例外。
- 日常源码、已保存场景/资源和配置优先直接编辑文件；仅在当前内存文档、Undo、运行和调试需要时使用 MCP。操作 Godot 编辑器会话通过 MCP，禁止 Computer Use；实际会话能力缺失时记录到目标工程的 docs/godot_mcp_lack.md 并告知用户。

## 开发、构建与同步

- 插件指南：addons/godot_mcp/README_zh-CN.md。
- 文档入口：addons/godot_mcp/docs/README.md；工具契约：addons/godot_mcp/docs/tool_catalog.json。
- 薄补丁、构建参数和上游维护：addons/godot_mcp/docs/engine_patches.md。
- 当前版本 0.6.1 / 协议 2，默认展示 24 个工具，完整注册 62 项。9 个单节点入口统一为 edit_batch，扩展能力通过 discover/call 按需使用。旧 25 个 MCP 别名已删除；不要重新增加兼容层。
- 改动生产文件后运行 scripts/deploy.py --manifest（相对插件目录），生成哈希清单，再重启测试编辑器。同步脚本遇到部署冲突必须停下检查，不能强行覆盖。
- docs/ 只保留当前使用、契约和必要维护说明；不保存开发流水、阶段计划、测试报告或性能测量。运行产物输出到系统临时目录或 CI 制品。
- Python 检查与实际 E2E 使用 tests/ 下脚本；进程表检查显式设置 GODOT_MCP_PROCESS_TESTS=1。测试清理精确匹配临时工程与可执行文件，确认进程退出，不杀无关工程。
- 构建标识可用 BUILD_NAME 配置；macOS 使用生产 arm64 构建，vulkan=no 不禁用 Metal。

## 正式发布目录

release/ 是已验证的最终交付快照，受现有 [Rr]elease/ 忽略规则排除，不是日常开发目录。

- 不直接编辑 release/，不向其中写入中间构建、日志或未验证插件。
- 只有用户确认功能修改完成，且构建、签名、插件 Python/完整 E2E、API XML 校验全部通过后，才能更新正式 release/。
- 发布先创建独立 .release_staging/，用 ditto 保留 App bundle 元数据和签名，复制匹配插件、指南和 manifest；验证暂存 App、版本与文件一致性，再整体替换 release/。
- 任一步失败保留现有 release/，删除失败的暂存目录。正式替换后再从 release/ 执行一次真实 E2E。
- 不为发布 App 修改 Git 忽略规则；大型二进制使用制品存储或 Git LFS。

本轮开发更新 bin/ 构建与业务工程部署副本；旧正式 release/ 未更新，不据此声称它包含新能力。
