# 变更记录

## 界面外观与导航整理 · 2026-11-02

只调整呈现。字段、金额、评分、导入预检和确认写入的规则没有改。

- 用 `.streamlit/config.toml` 固定浅色主题：暖灰底、白色面板、深绿主操作，侧栏用深绿色。补充少量本地 CSS（已安装的文泉驿字体、指标卡片、隐藏 Deploy），不加载外部字体或组件。
- 侧栏收成六个工作区：工作台、选品、内容、经营核对、决策、数据。页面显示中文名称。去掉与「导入文件」和订单页重复的「订单费用导入」。异常复盘只留在经营核对，提案页只保留创建和审核。
- 页面上不再出现 P1–P5 这类阶段代号。规则评估写明是固定规则和模板，没有接入真实模型。合成数据提示留在侧栏。

## A46 界面交互：直接导航、分类提醒与汇总优先 · 2026-11-02

用户要求“优化界面交互”。本轮沿界面方案落实导航与工作台的首轮交互，不重做业务流程或实施全部视觉方案。保留此前未提交修改；A44 暂时无法实机验收、暂无业务资料的用户反馈保持为未验证，原 8531 临时服务已通过其持有的执行会话停止，随后健康接口不再响应。

- 品牌移入侧栏，当前工作区子页直接显示为文字选项；只有一个子页时显示静态标题。保留原路由与稳定对象定位，导航测试只适配控件类型，原业务断言继续保留。
- 工作台将五类成功读取的计数集中呈现，下方只展示所选类别。首次优先有事项的组，此后保持人工选择；空组与读取失败分别显示。各类不合计，提案独立快照、订单四组一次报告及原金额口径不变。
- 类别与各组页码在分类切换、进入详情或其他页面再返回后保留；列表减少时收回有效页码，切空间清除旧上下文。扩展往返用例实际复现两项上下文重置后修复，没有用第一条记录替代目标。
- 离线协作去掉单选项任务下拉框，先看汇总和可用操作，再展开实际过程与各角色来源。成本、复核、汇总失败仍在折叠区外显示，未复核、部分运行和来源失效的限制不变，未知贡献没有补零。
- 新增 `tests/test_ui_interactions.py` 并加入逐文件清单；11 项覆盖分类、分页/收回、跨页、只读、两组读取错误隔离、空间清理和协作结果/失败层级。生产改动限 app.py 及导航、工作台、协作三个 UI 模块，没有改业务字段、数学、评分、数据库、依赖、导入或审批服务。

验证使用现有 Windows / Python 3.14.6 / Streamlit 1.66.1，实际命令和结果：

| 实际命令或检查 | 结果 |
| --- | --- |
| `.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests/test_ui_interactions.py --tb=short --show-capture=no`（最初实现前） | 7 failed，缺新导航与分类控件 |
| 同一 pytest，仅 `-k 'manual_selection or keeps_its_page'`（扩展详情往返后、修复前） | 2 failed，复现类别和页码重置 |
| pytest 的 `tests/test_ui_interactions.py tests/test_r1_ui.py tests/test_m1_ui.py tests/test_database_recovery_ui.py tests/test_recovery_harness.py`（最终相关） | 69 passed，15.21s |
| `.\.venv\Scripts\python.exe -m pytest -q -rs -p no:cacheprovider`（首次源码全量，扩展往返及提案读取失败用例前） | 480 passed、6 skipped，54.18s |
| `.\.venv\Scripts\python.exe scripts/verify_offline.py` | 34 项通过，备份副本重读一致 |
| `.\.venv\Scripts\python.exe -m ruff check .` / `-m pip check` / `git diff --check` | 通过 |
| 独立合成预览 AppTest，限制外网后走“结算分类 → OL-05 → 差异” | 页面与导航成功，正确定位 OL-05 / −0.50；不作为浏览器结论 |
| `python -m streamlit run <visualizations>/interaction_preview.py --server.address 127.0.0.1 --server.port 8532 --server.headless true --browser.gatherUsageStats false` | 独立合成预览启动，健康接口 200 / ok；普通业务库未使用 |

最终当前源码的完整回归在新的中文空格目录中对交付包执行，记录见 `user_data/xhs-seller-copilot-offline-20261102-interaction-r5.zip.verification.json`，包含确切命令、跳过项、包哈希及源文件字节一致性。包、SHA256 与报告沿用显式清单交付，旧包保留；临时预览脚本/数据库位于 Codex 可写的 visualizations 目录，不入源码包。

前端验证环境：Browser 插件/技能未提供；此前 Windows 浏览器 URL 识别策略停止仍有效，没有换用 Playwright 绕过。没有本轮真实浏览器 URL/DOM、控制台、截图、桌面/窄屏或键盘证据。这里只证明隔离 AppTest 中页面结构和交互状态，健康接口只证明服务启动。首次源码全量的 6 项跳过是 5 项进程信息受限的原生启动器检查和 1 项符号链接权限检查；最终包中的实际跳过另见报告。

下一入口是按可用的实际交互反馈继续商品/内容或核对/导入切片。完整视觉方案、A44 实机与真实业务人工核对均未标为通过，没有声称操作耗时或转化率改善。

## A44 剩余验收准备：随包隔离入口与人工清单 · 2026-11-02

用户在“继续”后明确选择“完成离线版剩余验收”。核对现有状态发现，A44 合成故障验收入口和清单只存于被忽略的 `user_data/manual-acceptance-20261101-r2/`，源码包缺少可复现的人工验收准备。本轮将其整理为随包交付材料；没有把准备完成或 AppTest 通过当作实机与真实业务验收完成，目标仍待实际结果。

- 先扩充 A44 和入口缺失回归，确认 1 failed、6 errors，再交付 `scripts/recovery_acceptance.py`、`docs/RECOVERY_ACCEPTANCE.md` 和 `tests/test_recovery_harness.py`，显式清单增加这 3 个文件。
- 验收入口仅新建合成目录、演示库和独立空库，固定运行普通 app.py，不使用项目现有数据库。每次注入只触发一次，支持取消未触发的故障；初始化失败覆盖 BEGIN IMMEDIATE。上下文包装在每轮结束时恢复，普通入口不包含故障控件；只支持一个验收标签，共享控制的限制已说明。
- 8 项隔离回归验证所有引擎路径均在测试临时目录、旧表单撤下、原 SKU 定位、真实提交前回滚和提交后无重复、提案终态与完整审计、预检失效、备份可达和空间切换。清单对照实际页面修正了内容审计所在的“修订历史”标签。
- 通过端口可用性检查后启动独立 `127.0.0.1:8531` 验收服务，健康接口实际为 200 / ok；保留服务供用户按清单检查。未操作浏览器，也未操作原有服务。健康检查不证明应用页面已渲染或走查通过。
- README、TASKS 和验收案例明确区分工具交付、自动化、A44 实机和真实业务人工核对；后两项仍未勾选。没有修改业务字段、数据库结构、金额/评分、依赖或普通应用 UI，没有启用 M2/M3/P6。

实际命令与结果（Windows / Python 3.14.6）：

| 实际命令或检查 | 结果 |
| --- | --- |
| `.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests/test_recovery_harness.py --tb=short --show-capture=no`（交付前） | 1 failed、6 errors，缺随包入口 |
| 同一命令（最终） | 8 passed，2.68s |
| pytest 的 `tests/test_recovery_harness.py tests/test_database_recovery_ui.py tests/test_import_preflight_recovery.py tests/test_backup.py tests/test_offline_package.py` 相关回归（新增最后一项前） | 101 passed、1 skipped，15.13s |
| `.\.venv\Scripts\python.exe -m pytest -q -rs -p no:cacheprovider`（源码全量） | 470 passed、6 skipped，50.11s |
| `.\.venv\Scripts\python.exe scripts/verify_offline.py` | 34 项通过，备份副本重读一致 |
| `.\.venv\Scripts\python.exe -m ruff check .` / `-m pip check` / `git diff --check` | 通过 |
| `.\.venv\Scripts\python.exe -m streamlit run scripts/recovery_acceptance.py --server.address 127.0.0.1 --server.port 8531 --server.headless true --browser.gatherUsageStats false` | 独立验收服务启动；urllib 请求 `/_stcore/health` 返回 200 / ok |

新版交付使用 `user_data/xhs-seller-copilot-offline-20261102-acceptance-r4.zip` 和 SHA256 副文件；整包哈希、解压复验命令及结果另存 `.zip.verification.json`。旧包保留，验收合成库不入包。

6 项跳过仍为 5 项进程读取受限的原生启动器检查和 1 项符号链接权限检查。浏览器工具此前无法识别 URL 的策略停止仍有效，本轮没有重试或绕过。下一步是用户在 8531 按清单回报桌面/窄窗结果，以及在日常真实空间用本地合法去身份化资料回报业务核对范围与结果；没有资料时保持未执行。完整剩余验收目标未标为完成。

## A45 补强：损坏源码包的明确失败反馈 · 2026-11-02

按“继续推进开发”沿既定离线可靠性范围检查交付工具，保留进入本轮前的所有未提交修改。发现整包哈希已匹配时，不支持的压缩方法或损坏 DEFLATE 数据会从校验器抛出未处理异常，命令没有约定的失败 JSON；本轮只修复 A45，不启动 M2/M3/P6。

- 先扩充 A45，再增加真实 ZIP 字节损坏、未知压缩方法和深层 JSON 清单回归；修复前 2 failed、1 passed，分别复现 NotImplementedError 和 zlib.error。深层清单在当前 Python 3.14 已被既有结构校验拒绝，兼容较早 Python 的递归解析异常处理未声称跨版本实测。
- 校验仅接受随包构建器使用的 DEFLATE 及未压缩成员；压缩读取失败统一为校验错误，JSON 递归解析失败也明确拒绝。CLI 返回失败 JSON 与非零退出码，不显示原始异常堆栈。
- 新回归实际核对 ZIP、SHA256 和源码字节不变，目录没有新增解压文件；现有有效包、可复现构建、逐文件哈希和错误路径继续验证。未改业务模型、数据库、金额/评分、依赖、UI 或外部连接。

Windows / Python 3.14.6，实际命令与结果：

| 实际命令或检查 | 结果 |
| --- | --- |
| 变化前全量 pytest（`-q -rs -p no:cacheprovider`） | 459 passed、6 skipped，53.35s |
| 同一 pytest 参数，`tests/test_offline_package.py -k unreadable --tb=short --show-capture=no`（修复前） | 2 failed、1 passed |
| 同一 pytest 参数，`tests/test_offline_package.py`（修复后） | 35 passed、1 skipped，1.61s |
| `.\.venv\Scripts\python.exe -m pytest -q -rs -p no:cacheprovider`（最终源码全量） | 462 passed、6 skipped，49.55s |
| `.\.venv\Scripts\python.exe scripts/verify_offline.py` | 34 项通过，备份副本重读一致 |
| `.\.venv\Scripts\python.exe -m ruff check .` / `-m pip check` / `git diff --check` | 通过 |

交付使用 README 的 `package_offline.py build/verify` 命令和新文件名 `user_data/xhs-seller-copilot-offline-20261102-package-r3.zip`，旧包保留；精确哈希及新目录解压复验的实际命令、结果另存同名 `.zip.verification.json`，与 ZIP 分离。

6 项跳过为 5 项受进程读取限制的 Windows 原生启动检查和 1 项文件符号链接权限检查。未进行浏览器或真实业务资料验收，A44 实机走查仍保持未验证，未重试此前被工具策略停止的操作。下一入口仍是允许环境下的 A44 补验与合法去身份化 CSV 的人工业务验收；后续外部能力沿用原授权门槛。

## 离线交付收尾：显式清单与可复现源码包 · 2026-11-01

继续用户确认的离线可靠性与使用体验方向，完成不依赖浏览器的交付收尾。保留既有未提交修改。先补 A45 及入口缺失用例，确认随包构建命令不存在，再实现标准库工具；没有修改业务模型、公式、依赖、数据库或平台连接。

- 新增 `scripts/package_offline.py` 和逐文件白名单 `scripts/offline-files.txt`，明确收录 131 个源码、说明、配置、测试、空白模板及合成样例。运行时不递归搜集目录，不依赖 Git，不读取业务数据库，不收录虚拟环境、Git、用户资料、运行日志或缓存；缺文件/越界/重复/链接时整次拒绝。新模块必须显式加清单，回归同时检查现有代码和已声明合成 CSV 是否全部随包。
- 固定 ZIP 成员顺序、时间与权限元数据，逐文件生成大小及 SHA256 清单，旁附整个 ZIP 的 SHA256；同一环境、同一输入字节重复构建的 ZIP 一致。构建不覆盖已有文件，副文件写入失败清理本次新输出；校验不解压/执行，检查整个包、成员集合、包内清单及各文件内容。哈希不是签名或来源真实性证明。
- 增加 33 项打包回归，其中 32 项通过；原生 Windows 目录联接实测拒绝，文件符号链接因当前系统不允许创建而跳过。包含缺文件、路径/大小/哈希/清单篡改、重复项、私有资料排除、异目录/中文路径、输出保护及失败 JSON/退出码。
- 全量回归实际发现 Windows 管道错误消息按本地编码输出，导致 `json.loads(bytes)` 失败；已改为与代码页无关的 JSON 字符转义，保留原中文消息，验证错误输出仍可解析。篡改测试补齐正确的外部哈希后再测试包内成员，避免只因缺 SHA256 副文件而误判内部校验已覆盖。
- 候选包解压到新的中文空格路径，使用现有已安装依赖的 Python 完成全量回归、34 项业务闭环和静态检查；不携带、复用或修改现有 demo/local 数据库。正式 ZIP、`.zip.sha256` 与独立 `.verification.json` 位于 `user_data/`；正式包的精确哈希和逐项复验保存在外部报告，避免把报告写进其自身哈希范围。

实际命令与结果（Python 3.14.6 / Windows）：

| 实际命令或检查 | 结果 |
| --- | --- |
| `.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests/test_offline_package.py -k builder_is_shipped --tb=short`（实现前） | 1 failed，确认缺少交付构建脚本 |
| `.\.venv\Scripts\python.exe -m pytest -q -rs -p no:cacheprovider`（初次全量） | 458 passed、1 failed、6 skipped，56.69s；失败为上述 Windows 错误 JSON 编码 |
| `.\.venv\Scripts\python.exe -m pytest -q -rs -p no:cacheprovider tests/test_offline_package.py`（修复后） | 32 passed、1 skipped，1.16s |
| 标准库 `build_package` / `verify_package`，再将候选 ZIP 解压至 `user_data/offline-package-qa-20261101-ekw8v47w/离线源码 clean copy v2` | 131 个源文件及清单完整 |
| `D:/AI_Dev/Project/电商/.venv/Scripts/python.exe -m pytest -q -rs -p no:cacheprovider`（上述新目录） | 459 passed、6 skipped，58.90s |
| 同一 Python 执行 `scripts/verify_offline.py`（上述新目录） | 34 项合成业务闭环通过，备份副本重读一致 |
| 同一 Python 执行 `-m ruff check --no-respect-gitignore .`（上述新目录） | All checks passed；显式关闭祖先 user_data 忽略规则，确保实际检查解压源码 |
| `.\.venv\Scripts\python.exe -m pip check` / `git diff --check` | 通过 |

正式构建和校验可用 README 的两条 `package_offline.py build/verify` 命令重现，既有包均保留。6 项跳过分别是 5 项受进程读取限制的 Windows 启动检查和 1 项文件符号链接权限测试；不能据此声称这些路径本轮已经原生验收。文件清单只控制收录范围，不会识别有人写入已列源码/合成示例中的秘密。包不含依赖安装包，未在另一台机器全新安装；未做真实经营资料人工验收。本轮未操作浏览器，A44 实机补验仍因上轮工具 URL 识别策略停止而保持未验证。下一入口为允许的浏览器环境补验及合法去身份化 CSV 的人工业务验收，M2/M3/P6 仍不启用。

## 离线可靠性补强：页面故障隔离 · 2026-11-01

按用户“继续”的持续目标，沿已确认的离线可靠性与使用体验方向推进。保留开始时所有未提交修改；本轮未提交或回退既有工作。先检查当前工作树、A43 记录及初始化/内容/提案等入口，运行 391 项通过的基线，再补 A44 和首批 24 项回归，确认未捕获的数据库异常会显示原始 SQL 与错误后实现。

- 新增 `ui_database.py`，在 app.py 为初始化和完整页面增加 SQLAlchemy 数据库异常边界。页面渲染使用可撤下的容器；失败后清除部分表单、金额、历史和下载，显示固定提示与主动重新读取入口。故障会话普通重绘不再运行原页面或动作；备份在初始化检查与故障门槛之外仍可到达。已有工作台、预检和回执局部处理不被替代。
- 主动恢复清除旧预检、回执、会话下载和未提交表单，重读当前来源；保留空间、页面、子操作、报告时点和稳定对象。P1 六类实体均可恢复原 ID；为研究、关联及核验补充仅限编辑页的导航目标验证。原目标被删除仍按既有规则报错，不自动改选另一条。
- 在 P1/P3/P4/P5 保存与决定表单加入仅恢复后变化的标识，清除 Streamlit 隐式控件可能带回的旧值。仅删除可枚举的 session_state 不足以完成此事，回归已实际复现并验证修复；无故障时原控件标识与操作方式保持。
- 六类写操作在审计 INSERT 已执行但未提交时注入异常，完整 SQL dump 验证业务与审计全回滚；事实保存及提案决定另在真实 COMMIT 后刷新时注入错误，恢复后重读已提交状态，不重复写入或改变终态。因此提示不声称“未保存”或“已回滚”。
- 故障清理取消会话内离线任务并撤下旧运行/报告，继续复用同一个线程资源池；阻塞线程尚未返回时仍占用原名额，迟到结果不恢复任务。屏障测试验证此行为，没有创建可绕过名额的新执行器。
- 本轮生产修改为 app.py、一个新故障恢复模块、导航及 P1/P3/P4/P5 的表单标识；新增 `tests/test_database_recovery_ui.py`，更新四份说明。没有改字段、金额/评分公式、数据库结构、依赖或事务服务，没有读取用户经营资料或调用外部模型/平台。

沿用 Windows / Python 3.14.6；隔离合成测试禁止外网。实际命令和结果：

| 实际命令 | 结果 |
| --- | --- |
| `.\.venv\Scripts\python.exe -m pytest -q -rs -p no:cacheprovider`（开发前） | 391 passed、5 skipped，39.69s |
| `.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests/test_database_recovery_ui.py --tb=no`（修复前） | 首批 24 failed |
| `.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests/test_database_recovery_ui.py --tb=short --show-capture=no`（扩充后） | 36 passed，8.13s |
| `.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests/test_import_preflight_recovery.py tests/test_p3_ui.py tests/test_p5_proposal_list.py tests/test_r1_ui.py tests/test_r3_ui.py --tb=short --show-capture=no` | 61 passed，12.31s |
| `.\.venv\Scripts\python.exe -m pytest -q -rs -p no:cacheprovider`（最终全量） | 427 passed、5 skipped，47.48s |
| `.\.venv\Scripts\python.exe scripts/verify_offline.py` | 34 项合成业务闭环通过，备份副本重读一致 |
| `.\.venv\Scripts\python.exe -m ruff check .` / `-m pip check` / `git diff --check` | 全部通过 |

首次针对性回归暴露了报告子操作丢失、隐式表单旧值仍在，以及一项读异常注入条件漏掉 WHERE 查询；前两项已修复，后者纠正测试条件，未删除业务断言。最终全量包含扩充后的全部 36 项用例。

5 项 Windows 原生启动检查仍因环境限制读取 TCP/进程信息跳过。实机走查尝试使用 computer-use 技能及返回的唯一 Edge 窗口：窗口最初最小化，重新选择和激活后，工具因无法可靠识别 Windows 当前浏览器 URL 而按策略停止；随后未继续浏览器输入或改用其他方式绕过。没有建立或修改浏览器验收数据库/服务，没有本轮实机或窄屏验收结论。自动化验证不替代真实业务资料人工验收。下一入口为在工具可识别 URL 的环境补验 A44 并整理更新后的离线交付包；M2/M3/P6 仍另按各自门槛安排。

## 离线可靠性补强：预检异常恢复 · 2026-11-01

按用户“继续推进开发”并明确选择“离线版可靠性与使用体验”，本轮限定既有预检路径。保留工作区已有未提交修改，没有提交或回退原工作。先读取当前契约及任务，运行基线，再补 A43 和 14 项用例；修复前全部失败，抽查确认 Streamlit 直接展示带底层数据库异常的执行错误。

- `ui_import_flow.py` 增加共用预检入口：调用前清除旧预检、回执和成功提示，仅成功返回完整预检才写入当前会话；捕获 SQLAlchemy 数据库异常，展示固定错误及主动重新预检指引，不回显 SQL、参数或异常原文。
- `ui_p1.py` 的手工编辑、CSV 与合成研究入口，以及 `ui_p4.py` 的上传/合成入口复用该处理。手工编辑失败不进入锁定状态，保留当前表单；未替换的 P4 文件继续保留人工来源确认。恢复后仍须用户重新预检、核对并确认，没有自动重试或提交。
- 新增 `tests/test_import_preflight_recovery.py`：在统一/原有入口注入真实预检读事务和失败摘要 INSERT 异常；验证旧预检或回执失效、重绘无旧确认入口、完整数据库 dump 不变、失败记录未半写入、恢复可重试、最终仅有人工确认的成功批次及预期审计。手工编辑同时覆盖读取失败与失败摘要写入失败。
- 本轮生产改动仅三个 UI 模块；另补验收、任务、README 和变更记录。未改业务字段、金额/评分公式、服务提交协议、数据库结构或依赖，没有读取用户业务资料或调用外部模型/平台。

环境沿用 Windows / Python 3.14.6，测试禁止外网。实际命令与结果：

| 实际命令 | 结果 |
| --- | --- |
| `.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider`（开发前） | 377 passed、5 skipped，39.19s |
| `.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests/test_import_preflight_recovery.py --tb=no`（修复前） | 14 failed，复现未捕获的预检异常 |
| `.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests/test_import_preflight_recovery.py`（修复后） | 14 passed，4.44s |
| `.\.venv\Scripts\python.exe -m pytest -q -rs -p no:cacheprovider`（最终全量） | 391 passed、5 skipped，40.89s |
| `.\.venv\Scripts\python.exe scripts/verify_offline.py` | 34 项合成闭环通过，备份副本重读一致 |
| `.\.venv\Scripts\python.exe -m ruff check .` | All checks passed |
| `.\.venv\Scripts\python.exe -m pip check` | No broken requirements found |
| `git diff --check` | 通过 |

5 项原生启动生命周期检查因当前环境限制读取 Windows TCP/进程信息而明确跳过，本轮不据此声称原生启动已验证。未进行本轮浏览器视觉走查或真实业务资料人工验收，UI 状态由隔离 AppTest 验证。新增处理仅覆盖预检阶段的数据库异常；数据库初始化、对象列表及其他页面的异常恢复仍需按实际用例逐项检查。下一入口为继续补强这些离线路径，或用合法去身份化 CSV 人工验收；M2/M3/P6 仍按各自门槛另行安排。

## 浏览器补验与数据空间标题修复 · 2026-11-01

用户要求“继续”，本轮补齐上一轮因浏览器接口/应用授权不可用而未完成的实机走查。仍限定完整离线版，不启动 M2/M3/P6。Edge 原有 `127.0.0.1:8501` 标签未被操作；创建临时 `127.0.0.1:8520` 标签和独立合成验收包装器，只写 `user_data/browser-qa-20261101b/` 内的测试库。

- 桌面实际比较 S-01 的 Q-01B 为 8.00 元，已保存 Q-01A 保持 8.40 元；S-03 缺获客费用仍不可完整计算，保本 CAC 为 8.60 元。进入内容页仍定位 S-03，缺事实与素材不填造内容。
- 在 390×844 窄屏检查内容、展开/收起导航、导入预检及订单来源。实际确认导入 P4 合成批次 46 条，再从订单 OL-04 进入统一导入，经浏览器文件选择器上传随包 `expense_actual_revision2.csv`。缺少费用不含退款确认时整批拒绝；确认后预检仅更新 1 条，原子提交并主动重新核对仍定位 OL-04，贡献 41.50 元，费用来源修订 2、金额 700 分，未记录结算保持未知。
- 实机发现标签标题写死“合成演示”，选择真实空间后仍显示旧名称。先扩充 A23，再将 app.py 的标题改为读取当前会话的数据空间。浏览器复核初始合成、真实空库、返回合成及普通重绘的标题均与侧栏一致；不更改业务选择、字段、金额、评分、权限、依赖或数据库结构。
- 首版临时包装器在重绘时重复包装引擎，导致测试数据空间路径被叠加重定向；此问题位于忽略目录内的验收脚本，不在生产应用。修正为每轮载入原工厂并绑定独立实例，启用全新的 v2 验收库，重新执行订单导入、实际上传、拒绝/修订/重新核对及空间切换。只读 SQLite 核对：合成库 3 候选/6 报价/3 SKU/5 订单/2 成功批次/47 修订，真实库上述计数全部为 0；没有用首版包装器结果宣称业务空间隔离已通过。
- 页面异步重绘时只依据完成后的稳定 DOM 与实际截图核对；中途调整了过早操作控件与误用标题选择器的验收动作，没有为工具超时删除业务断言或改变应用校验。
- 源码交付包更新为 `user_data/xhs-seller-copilot-offline-20261101-browser-r1.zip`，保留原包。新包仅收录显式源代码清单及 SHA256 清单，不收录测试库、截图、日志、虚拟环境或私人资料。

| 实际命令或检查 | 结果 |
| --- | --- |
| `powershell.exe -NoProfile -ExecutionPolicy Bypass -File 'user_data/browser-qa-20261101b/scripts/local-app.ps1' -Port 8520 -NoBrowser -PythonPath 'D:/AI_Dev/Project/电商/.venv/Scripts/python.exe'` | 独立合成验收服务就绪 |
| Edge 浏览器桌面及 `viewport.set({width:390,height:844})` | 完成上述报价、内容、导入、真实文件上传、拒绝及订单修订路径；视口最后 reset，临时标签关闭 |
| `.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp=user_data/browser-followup-tests-20261101` | 382 passed，58.90s，含 Windows 原生启动器，无跳过 |
| `.\.venv\Scripts\python.exe scripts/verify_offline.py` | 34 项通过，`backup_reopened=true` |
| `.\.venv\Scripts\python.exe -m ruff check .` / `-m pip check` / `git diff --check` | 全部通过 |
| 只读 SQLite 查询 v2 的 demo/real 六表计数及 E-04-marketing 版本/金额 | 合成与真实测试空间正确隔离，修订金额与浏览器一致 |
| `powershell.exe -NoProfile -ExecutionPolicy Bypass -File 'user_data/browser-qa-20261101b/scripts/local-app.ps1' -Port 8520 -Stop -NoBrowser` | 临时服务停止；原 8501 服务未操作 |

截图位于已忽略的 `user_data/browser-qa-20261101b/`，包括 `quote-comparison.png`、`content-narrow.png`、`order-revised-desktop-v2.png`、`order-revised-narrow-v2.png` 和 `real-space-title-fixed.png`；均为合成记录或空库。测试包装器及 v2 库保留供复查。本轮只补验列出的操作，没有宣称所有页面均完成视觉检查、全部失败/并发路径已在浏览器执行、或已用真实业务资料验收。后续仍为合法去身份化 CSV 的人工业务验收；真实模型、长期任务及平台连接按原门槛另定范围。

## 完整离线版交付 · 2026-11-01

用户要求完成完整项目，并明确选择“先完成并验收完整离线版”。本次收尾范围为既有 P0–P5、M1、R1–R3 与 A41–A42；M2 真实模型、M3 持久任务和 P6 平台接口保留为可选后续。保留进入本轮前的所有未提交工作，未提交 Git、未回退原修改，未更改业务字段、公式、数据库结构或依赖。

- 发现 `start.cmd` / `stop.cmd` 依赖 Git 忽略的 `local_data/deployment.ps1`，干净副本缺少启动实现。改为随项目交付 `scripts/local-app.ps1` 与 `scripts/start-server.py`；支持本机端口、环境路径、不打开浏览器、并发防重和精确进程归属。其他程序/另一项目占用端口时拒绝启动和停止，日志按端口保存在忽略目录。启动失败停止本次自建进程，不把进程检查失败当作空端口。
- 原生测试发现 PowerShell 直接启动后台 Python 会继承测试输出句柄，健康接口成功后命令仍不返回；改用标准库子进程显式关闭继承句柄并隔离标准输入/输出，隐藏后台窗口。启动/停止超时使用单调计时；停止前再次核对进程创建时间与命令行。
- 新增 `scripts/verify_offline.py`：临时合成数据库的 34 项跨功能闭环，覆盖预检/提交、不变重传、经济性、评分、事实草稿、只读角色、订单退款/回收/修订、结算、提案待办与备份重读。固定合成时钟，不依赖执行日期；应用真实时点及过期规则不变。金额与既有服务一致，修订后贡献小计 134.50、累计到账 245.50 均仅为合成验收值。
- 备份只写临时新文件，对比重读后的全部表、订单报告和提案审计；不增加覆盖业务库的恢复功能。新增 8 项回归，并在子进程禁止网络、核对现有两库文件哈希不变；缺少合成文件时命令非零退出且不假报成功。
- 干净副本仅复制显式列出的 124 个源代码、测试、文档、依赖、模板与合成文件，不复制数据库、真实环境变量、日志、素材或虚拟环境。副本在无外网条件通过闭环；AppTest 检查合成空间与真实空库各 13 个页面入口，共 26 项，无异常/错误。副本的 `start.cmd` / `stop.cmd` 在本机 8516 实际启动和停止真实应用，验收后服务关闭。
- 更新 README、TASKS、ACCEPTANCE_CASES，并修正设计/存档中的过时下一阶段入口。源码 ZIP 从同一显式清单生成，含 SHA256 清单，位于被忽略的 `user_data/xhs-seller-copilot-offline-20261101.zip`；不包含本地数据库、秘密、日志、素材或依赖安装包。

环境：Windows、现有 Python 3.14.6 / Streamlit 1.66.1 / SQLModel 0.0.48 / Pydantic 2.14.1；未安装新依赖、未重新生成锁文件。

| 实际命令或检查 | 结果 |
| --- | --- |
| `.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider`（开发前） | 374 passed，34.80s |
| `.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests/test_local_launcher.py --tb=short`（实现前） | 1 failed、5 errors，复现入口依赖缺失 |
| `.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests/test_offline_delivery.py --tb=short`（实现前） | 2 failed，闭环命令尚不存在 |
| `.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests/test_local_launcher.py --basetemp=user_data/launcher-qa-20261101-b --tb=short` | 6 passed，18.64s；中文空格路径、并发/重复启动、两种端口冲突保护、重复停止、缺失文件 |
| `.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests/test_offline_delivery.py --tb=short`（实现后） | 2 passed，1.28s |
| `.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp=user_data/offline-full-qa-20261101-a`（最终完整回归） | 382 passed，58.98s，包含原生启动器测试，无跳过 |
| `.\.venv\Scripts\python.exe scripts/verify_offline.py` | `status=passed`，34 项，`backup_reopened=true` |
| 在 `user_data/offline-source-qa-20261101` 以原环境 Python 执行 `scripts/verify_offline.py` | 干净源代码副本同样 34 项通过 |
| PowerShell 内联合成验收脚本经原环境 Python 执行 AppTest（副本 app.py） | 26 个页面入口通过，禁止外网、允许框架本机回环 |
| 副本 `start.cmd -Port 8516 -NoBrowser -PythonPath 'D:/AI_Dev/Project/电商/.venv/Scripts/python.exe'` / `stop.cmd -Port 8516 -NoBrowser` | 实际应用服务启动、健康响应并停止 |
| `.\.venv\Scripts\python.exe -m ruff check .` / `-m pip check` / `git diff --check` | 全部通过 |

原生测试最初因沙箱拒绝读取 Windows TCP/进程信息未能执行；移至宿主终端和事先检查不存在的项目内独立 QA 目录后完成，未关闭进程归属检查。常规受限环境测试会明确跳过原生部分，不把它报为已验证。中途修正了验收脚本的 InputFile 属性名、ruff 格式问题及临时 UI 检查对框架回环的误拦截；最终命令均通过。

本轮浏览器人工走查未完成：浏览器接口不可用，原生 Edge 应用授权等待超时；没有绕过授权。26 项 AppTest 是界面自动化验证，不作为人工视觉或窄屏验收声明。安装依赖仍需要包源，ZIP 不含虚拟环境；未在另一台机器新装依赖。未导入真实业务资料，未验证市场、履约、素材权利或平台权限，没有真实模型推理/账单或经营执行。后续入口为合法去身份化 CSV 的人工业务验收；M2/M3/P6 按各自范围另行安排。

## R3 收尾：重新核对导航与回执异常 · 2026-11-01

按用户“继续推进”复查已完成 R3 的边界路径，保留原有未提交工作。开发前全量基线为 360 passed；先扩充 A40，新增 5 项 UI 和 9 项服务回归，在修改实现前分别确认失败，再做最小修复。

- 原订单页切到“CSV 导入”提交后，尚未选订单时，“按当前时点重新核对订单”只更新时点却仍停留导入页。现在显式选择“报表与核对”，已选订单仍沿用原定位规则。
- 回执服务验证计数 JSON 的结构、非负整数类型、拒绝数为零及带时区确认时间；不合法元数据以固定错误拒绝，不补零或修复数据库。此检查不证明批次内容真实性，也不检查全部导入审计连续性。
- 两组导入共用的回执展示捕获读取异常和无效元数据，不回显 SQL/原始输入，不显示成功回执或其操作按钮；明确读取失败不表示已提交事务回滚。会话保留批次 ID，恢复后重绘可重读原批次。隔离测试确认无重复批次、审计或业务写入。
- 生产修改限于 `services/imports.py` 的回执读取和 `ui_import_flow.py` 的展示/导航；另补两个 R3 测试模块、A40、README 和任务记录。无字段、公式、依赖或数据库结构变更。

沿用 Windows / Python 3.14.6；离线测试禁止外网、无 Key。实际验证：

| 实际命令或检查 | 结果 |
| --- | --- |
| `.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider`（开发前） | 360 passed，37.88s |
| `.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests/test_r3_ui.py -k 'legacy_import or receipt_failure'`（修复前） | 5 failed，复现导航错误及回执未捕获异常 |
| `.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests/test_r3.py -k invalid_receipt --tb=no`（修复前） | 9 failed，复现无效元数据未安全拒绝 |
| `.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests/test_r3.py tests/test_r3_ui.py`（修复后） | 26 passed，4.70s |
| `.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider`（最终全量） | 374 passed，35.48s |
| `.\.venv\Scripts\python.exe -m ruff check .` | All checks passed |
| `.\.venv\Scripts\python.exe -m pip check` | No broken requirements found |
| `git diff --check` | 通过 |
| `.\.venv\Scripts\python.exe -m streamlit run user_data/r3_ui_fixture/app.py --server.port 8514 --server.address 127.0.0.1 --server.headless true --browser.gatherUsageStats false` | 复用独立合成验收包装器；完成浏览器走查后停止 |
| Edge 桌面走查 | 订单页 CSV 导入 → 合成预检 46 条不变 → 确认回执 → 未选择订单时重新核对 → 正确进入报表，净销售合计 350.00 元；临时标签关闭 |

截图保存在已忽略的 `user_data/r3_ui_fixture/recheck-verified.png`。浏览器仅在独立验收库提交不变批次，未写现有真实/演示业务库；本轮未重做窄屏、CSV 文件上传或人工业务验收。回执异常/恢复使用自动化隔离注入验证，未在浏览器损坏数据。中途一项测试夹具提交后读取已脱离会话的对象导致两项失败，已改为提交前保存批次 ID；最终全量通过。

下一入口仍为合法去身份化 CSV 的人工业务验收；M2 需明确供应商、数据范围与预算，P6 需单独授权。本轮未启动新阶段或外部调用。

## R3 导入与经营核对收敛 · 2026-11-01

按用户“继续推进开发”沿 README 的 R3 入口实施。保留已有 P5/M1/R1/R2、文档及启动脚本未提交修改，未提交或回退原工作。开发前完整基线为 343 passed；先补 A37–A40 和首批用例，确认 5 项因新能力未实现而失败后实现。

- 新增 `ui_import_center.py`、`ui_import_flow.py`：数据管理默认进入统一导入，保留旧入口和手工编辑。两类流程共用模板、批次/文件预览、差异、警告分页、错误报告与提交回执；原预检和原子提交规则不变。
- P4 支付口径、费用不含退款、整行本金三个确认仍随相应文件变更失效；切组/空间清除旧预检与结果。新入口复跑来源确认和替换文件用例；真实空间无合成入口。
- 成功回执从 `ImportBatch` 读取真实确认时间和计数，直接定位本批修订；批次失效不自动打开别的批次。成功后重绘移除旧确认表单，避免重复点击。SQLAlchemy 写入异常安全报错，不展示成功回执或原始输入；注入两类 INSERT 失败验证业务、批次和审计全回滚。
- P4 报告在原读取事务内附带来源快照；来源 ID/revision 与金额使用同一批对象，不另发查询。订单页展示分类成本、费用池、分摊、调整、结算来源及依据索引。完整 SQL dump 对比验证只读；WAL 并发修订测试验证来源与结果不混用版本。
- 订单可携带 ID 往返统一导入；提交后显式重新核对才更新报告时点。补充测试发现 Streamlit 隐藏控件会清除原报告时间，已保留跨页时点并维持切空间清理。OL-04 获客费用修订后，旧时点拒绝计算；重新核对显示实际贡献 41.50 元，保持 OL-04 定位。
- 全量回归发现备份测试把导航途中新增的空白模板按钮计为数据库备份，已仅捕获 SQLite 下载，保留隔离空间、快照内容及失败清除断言。
- 本轮主要变更为两个 UI 模块、app/导航/P1/P4 页面、导入回执读取、报告来源快照、两个 R3 测试模块及 P4/备份回归和文档。未改字段、金额/评分公式、模型、依赖或数据库结构。

环境沿用 Windows / Python 3.14.6；测试禁止外网，无 Key。

| 实际命令或检查 | 结果 |
| --- | --- |
| `.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider`（开发前） | 343 passed，28.86s |
| `.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests/test_r3_ui.py tests/test_p4_ui.py tests/test_backup.py tests/test_report_default_time.py` | 47 passed，7.75s |
| `.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider`（最终全量） | 360 passed，31.82s |
| `.\.venv\Scripts\python.exe -m ruff check .` | All checks passed |
| `.\.venv\Scripts\python.exe -m pip check` | No broken requirements found |
| `git diff --check` | 通过 |
| `.\.venv\Scripts\python.exe -m streamlit run user_data/r3_ui_fixture/app.py --server.port 8514 --server.address 127.0.0.1 --server.headless true --browser.gatherUsageStats false` | 隔离合成验收服务启动并完成验证，之后停止；现有演示/真实库未用于此浏览器写入 |
| Edge 桌面、1280×900、390×844 | P4 预检新增 46 条、提交回执、重新核对、OL-01 来源、22 元费用池/10 元分摊及订单往返定位通过；窄屏摘要、来源折叠及统一导入步骤可见；视口复原、临时标签关闭 |

固定浏览器路径：统一导入 → 合成批次预检/确认 → 按当前时点重新核对 → 初选 OL-01 → 查看费用池及分摊 → 导入修订入口 → 返回 OL-01。跳错对象 0 次，订单初选一次，返回无需重复选择。未计时、未与改版前做人工效率比较。浏览器未实际上传 CSV 或执行失败/并发流程，这些由隔离自动化测试覆盖。验收截图为合成数据，临时包装器及数据库留在已忽略的 `user_data/r3_ui_fixture/`。

限制：来源仅展示索引，不读取证据文件；无历史快照回放、自动列映射或备份恢复。回执是已提交批次信息，不保证当前业务记录仍是该版本；刷新/重启不保证会话恢复，离开导入页可能需重新选文件和确认。R1–R3 已完成，下一入口为合法去身份化 CSV 的人工业务验收；M2 真实模型须另定供应商、数据范围与预算，P6 仍需单独授权。本轮无外部调用和经营执行。

## R2 选品与内容操作简化 · 2026-11-01

按用户“继续推进开发”沿 README 的 R2 入口实施。开始时已有 P5/M1/R1、文档与启动脚本的未提交修改；通过 `git status --short` 核对并保留，未提交或回退原有工作。先运行 329 项基线，新增 A33–A36 和首批 UI 用例，确认 5 项因新能力未实现而失败后再实现。

- 新增 `services/product_queries.py` 和 `ui_products.py`，从 app.py 移出商品列表/经济性展示。候选详情只展示当前对象；无 SKU 候选仍可查看。商品、报价与 SKU 使用同一只读事务快照，报价算术复用原经济性函数。
- 已保存报价与临时比较明确区分，售价/费用假设保持相同，未知金额与单件场景限制可见。浏览、选择和查询不产生修订；隔离库完整 SQL dump 前后对比、并发 WAL 写入用例验证只读性及读取一致性。
- 导航新增候选/报价/SKU 编辑定位。目标页重新读取并校验存在性与归属，清除旧预检/字段，进入手工编辑；SKU 换报价仍经预检与确认，产生同一原子审计。修订号/时间折叠，来源时间仍需人工核对。
- 内容页复用离线模板生成准备清单，展示事实/素材缺口和事实依据；选择已核实事实可填入未提交标题/正文。选择不保存或审核，来源变化时旧草稿停止编辑/下载；原事实原文、再次审核与强制待核实清单规则继续生效。
- 评分先呈现阻断、缺口和反驳，再显示总分；来源版本默认折叠。评分、金额、数据库结构、依赖与契约均未在本轮改变。
- 原 P0/P1 页面测试改为先选择相应商品/报价，保留原金额、未知和不匹配断言。实机发现候选切换后 SKU 下拉框残留旧标签：按候选隔离控件标识，重启临时服务后在浏览器确认 S-02 选择框与 PC-02/S-02 详情一致。
- 更新 README、PRD、TASKS 与验收文档；下一候选为 R3。本轮新增两个生产模块、两个测试模块，调整 app.py、导航及 P1/P2/P3 UI；其他已有未提交改动不是本轮新增工作。

环境：Windows、Python 3.14.6、Streamlit 1.66.1、SQLModel 0.0.48、Pydantic 2.14.1。沿用虚拟环境与依赖锁；测试禁止外网，不需要 Key。

| 实际命令或检查 | 结果 |
| --- | --- |
| `.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider`（开发前） | 329 passed，31.37s |
| `.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests/test_r2.py tests/test_r2_ui.py tests/test_r1_ui.py`（最终相关回归） | 24 passed，5.36s；R2 自身 14 项 |
| `.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider`（最终全量） | 343 passed，30.62s |
| `.\.venv\Scripts\python.exe -m ruff check .` | All checks passed |
| `.\.venv\Scripts\python.exe -m pip check` | No broken requirements found |
| `git diff --check` | 通过 |
| `.\.venv\Scripts\python.exe -m streamlit run app.py --server.port 8513 --server.address 127.0.0.1 --server.headless true --browser.gatherUsageStats false` | 临时本机服务启动；模块修复后重启复测，验收后停止 |
| Edge 实机：桌面、1280×900、390×844 | 商品详情、Q-01B 临时比较与准确编辑定位、S-02 跨页内容定位、窄屏场景限制和内容缺口可见；视口已复原、临时标签关闭 |

限制：浏览器未提交事实、草稿或导入数据；事实选择/保存、来源过期、并发、错误目标和真实空库由隔离自动化测试验证。未计时或做改版前后用户效率对照。内容仍限事实原文；使用体验与素材权利须人核对。界面读取是当前快照，无历史回放。没有新增业务表、迁移、模型或平台请求；未进入 R3/M2/P6。

## R1 导航、上下文与只读工作台 · 2026-11-01

按用户“继续推进开发”实施 README 中的下一阶段 R1。保留开始时 P5/M1、文档及启动脚本的未提交修改；未提交、回退或覆盖原有工作。先增加 A22–A26 测试，再实现导航与查询，没有改字段契约、金额/评分公式、依赖或数据库结构。

- 新增六组工作区和三个导航/工作台模块，复用原页面，保留 M1、历史、导入与备份入口；结构检查失败仍可进入备份。
- SKU 在选品、经济性、研究与内容之间传递；临时报价不写回。提案按当前排序计算所属页，可定位第 101 条；订单显示定位标题、金额和来源。目标不存在、类型或归属不符时明确拒绝，不选择另一条代替。
- 切换空间保留页面位置，清除对象、表单、预检及下载；保留原 revision 与审计检查。并发已决定提案展示终态，审计异常不能通过跳转绕过。
- 工作台提案计数/明细共用读取快照，订单四组复用单次 P4 报告；每组最多 50 项，分别标时点。结算异常去重、未分摊费用单列；缺失与估计不变成确认利润。读取异常不复用旧结果，也不显示假零。
- 原 UI 回归仅调整导航操作，保留业务断言。中途回归发现空库文案和切换空间后丢失页面操作位置，已修复；只读测试比较完整数据库及审计内容。

沿用 Windows / Python 3.14.6 环境。实际验证：

| 命令或检查 | 结果 |
| --- | --- |
| `.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests/test_r1.py tests/test_r1_ui.py` | 15 passed，3.92s |
| `.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider` | 最终 329 passed，29.36s |
| `.\.venv\Scripts\python.exe -m ruff check .` | All checks passed |
| `.\.venv\Scripts\python.exe -m pip check` | No broken requirements found |
| `git diff --check` | 通过 |
| `.\.venv\Scripts\python.exe -m streamlit run local_data/r1-acceptance-1y4p4x5l/app.py --server.port 8512 --server.address 127.0.0.1 --server.headless true --browser.gatherUsageStats false` | 独立合成库验收包装器启动成功；服务已停止，不使用现有业务库 |
| 浏览器 1280×900 / 390×844 | 提案跳转、OL-05 差额、S-03 跨页、真实空库、窄屏卡片与备份入口通过；视口复原、临时标签关闭 |

固定走查：工作台 → P-DEMO-1，工作台 → OL-05，选品 S-03 → 研究 → 内容。三条路径跳错对象 0 次，额外重复选择对象 0 次（SKU 初选一次）。工具自动化从展开提案组到目标定位提示约 3.605 秒，从订单点击到定位标题约 0.177 秒；包含不同的工具与渲染等待，不是人工完成耗时，也没有改版前对照，不能据此宣称效率提升比例。101 条分页、并发决定、坏审计、归属错误、报告失败和结构失败备份路径由自动化测试覆盖，没有在浏览器逐一重演。

限制：工作台只针对本地已录入记录，卡组之间不保证原子快照；无后台同步或历史回放。M1 仍为离线规则/模板；无模型、平台调用和自动经营操作。下一入口为 R2 选品与内容操作优化，先补其验收；本轮未实施 R2/R3/M2/P6。

## M1 离线协作闭环 · 2026-11-01

按用户“推进开发”沿 README 既定下一阶段实施 M1。开始时工作区已有 P5 审计/分页、方案文档及启动脚本的未提交修改；通过 `git status --short` 记录并保留，未提交或回退这些改动。

- 先在 DATA_CONTRACT 第 9 节补充唯一的内存运行结构、基线算法、产物引用和失败语义，再新增 A27–A32 自动化验收与实现。
- 新增 `agents/` 的只读快照、白名单工具、产物校验、调度及报告；无新增业务表、数据库迁移或模型依赖。一次 1–3 个 SKU，三个专业角色真实并行，复核/主角色汇总按依赖执行，各一次，无自动重试。
- 快照在显式只读事务中读取相关集合，包括历史失败审计；不可变序列化后释放读事务。基线覆盖新关联、删除、同修订内容变化、事实和素材。并发 WAL 写入测试验证各集合来自同一读取快照。
- 经济性与评分复用既有函数；P3 抽出纯模板预览，与保存草稿共用标题/正文/拍摄规则，保留原保存行为。M1 不写事实、草稿、提案或审计；完整数据库内容对比证明运行、报告及重跑无业务副作用。
- 每会话上限三个工作线程，默认单步 30 秒、执行全程 120 秒；可控时钟、同步屏障验证依赖、超时、取消与迟到结果。不能强杀的只读线程保留资源名额，资源未释放时拒绝重跑；控制线程终态结束，线程不会阻止进程退出。
- 原导航增加“离线协作”：任务范围、实际事件、状态、证据修订、人工建议和 Markdown 报告；来源失效停止正常报告导出/跳转。跳转携带 SKU 到原内容页，不自动保存。前端局部刷新跳转经实机发现并修复为整页刷新；任务终态及取消刷新重跑控件。
- 实际窄屏走查发现报价表限制易被横向滚动隐藏，改为逐报价卡片；未知金额、获客缺失与场景不匹配始终文字展示。页面无表格快捷 CSV 导出绕过报告时点检查。

本轮环境为 Windows / Python 3.14.6，Streamlit 1.66.1、SQLModel 0.0.48、Pydantic 2.14.1；未重建环境或更新原依赖锁。

| 实际命令或检查 | 结果 |
| --- | --- |
| `.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests/test_m1.py tests/test_m1_ui.py` | 35 passed；含取消页、空间隔离、来源失效及导航回归 |
| `.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider` | 最终 314 passed，24.35s；含原金额、导入、草稿、审计、分页与模式兼容测试 |
| `.\.venv\Scripts\python.exe -m ruff check .` | All checks passed |
| `.\.venv\Scripts\python.exe -m pip check` | No broken requirements found |
| `git diff --check` | 通过 |
| `.\.venv\Scripts\python.exe -m streamlit run app.py --server.port 8511 --server.address 127.0.0.1 --server.headless true --browser.gatherUsageStats false` | 临时本机服务启动成功，验收后已停止 |
| 浏览器：1280×900 / 390×844 | 三 SKU 运行、角色汇总、报告生成、跳转 S-03、真实空库与空间隔离通过；窄屏报价限制与未知成本可见，临时窗口尺寸已复原 |

最初 23 项服务测试通过但 pytest 缓存写入有目录警告；后续使用项目既定 `-p no:cacheprovider`，不涉及跳过业务测试。中途页面回归发现返回协作页丢失对象选择，已恢复上次运行范围；最终全量无失败。

限制：所有角色仍为离线确定性规则与模板，无真实模型质量/费用/延迟对照；无平台请求。超时和取消用可控替身验证，未把快速离线计算当真实模型验证。浏览器演示库只有现有 P1 数据时正确显示研究缺口，95.00 分由隔离 P1/P2 验收验证。任务与下载仅会话内，刷新/重启不承诺恢复；导出的文件是生成时快照，之后并发变化不能撤回文件。现有隐私规则与本地数据完整性仍需人工核对。下一阶段入口为 **R1（A22–A26）**；M2/M3/P6 未启动。

## 多 Agent 协作方案 v0.4 · 2026-11-01

用户进一步明确：电商应用内部的选品、内容、成本角色分工，由主 Agent 汇总。本轮仍为方案设计，未启动应用实现。

- 新增 MULTI_AGENT_DESIGN：主 Agent、选品、成本、内容、复核五个固定角色；同一任务快照、受限只读工具、并行依赖、证据与分歧、限额/超时/取消及来源失效规则。
- 首个任务限定最多三个已录入 SKU 的评估；M1 先做明确标记的离线协作，M2 才按需接真实模型，M3 再考虑持久任务与新流程。固定角色不要求使用不同模型。
- PRD、README、TASKS 和开发指令同步改为优先 M1；保留 R1–R3 作为后续体验路线。新增 A27–A32，全部待实施。
- 明确现有契约缺少运行数据结构，M1 必须先补唯一契约再编码。本轮不改 DATA_CONTRACT 或 SCORING_RULES，不将会写库的草稿创建接口伪装为只读工具。
- 查阅 Grok Bot 和 Multi Agent 官方资料，链接随方案保存；借鉴角色协作体验，不声称复现其内部实现，不启用真实平台或模型。

实际验证：

| 实际命令或检查 | 结果 |
| --- | --- |
| `git status --short`、`Get-Content`、`rg -n` | 核对现有状态、方案入口及 M1/R1 范围一致性 |
| 内联 Python 经 `.\.venv\Scripts\python.exe -` 执行 | 7 份文档、31 个本地链接、A01–A32 唯一编号、M1 十项与 R1 九项均待实施、代码围栏及 M1 入口通过 |
| `Get-FileHash -Algorithm SHA256` 前后对比 | 55 个代码、测试、依赖、启动及数据/评分契约文件未变，无删除 |
| `git diff --check` | 通过 |

本轮修改六份现有文档、新增一份设计文档，保留此前未提交工作。未运行 pytest、ruff、pip check、浏览器或模型质量/成本对照测试；设计验收不代表新能力已实现。下一阶段入口已改为 M1，旧 v0.3 的 R1 优先建议被本条取代。

## 体验与架构方案 v0.3 · 2026-11-01

本轮仅优化方案，按用户选择聚焦页面重组、操作简化和可维护性；未实施新界面或业务功能。

- 重写 PRD：9 个平级入口重组为 6 个工作区，补充工作台草图、呈现规范、三条操作路径、导航上下文及错误/空态规则。
- 保留既有技术栈、金额与评分服务、预检确认和审计；查询按实际复用需要抽取，不新增业务表或第二套规则。
- 将后续工作分为 R1 导航与只读工作台、R2 选品与内容、R3 导入与经营核对；只为下一批 R1 写详细清单和 A22–A26，均未标为完成。
- 更新 README 阅读顺序与当前目录示例，将旧 P0 指令替换为未来 R1 模板；说明历史 P0 三表三页约束的适用阶段，避免要求当前 P5 回退。
- 修改范围为 README、PRD、TASKS、ACCEPTANCE_CASES、CODEX_START_PROMPT、CHANGELOG 六份文档。保留进入本轮前的未提交修改；未更改应用、测试、依赖、启动脚本、数据库或数据契约/评分规则。

本轮实际验证：

| 实际命令或检查 | 结果 |
| --- | --- |
| `rg --files`、`Get-Content`、`rg -n`、`git status --short`、`git diff` | 静态核对文档、实现入口和已有工作区修改 |
| PowerShell 内联脚本经 `.\.venv\Scripts\python.exe -` 执行 | 六份文档的 24 个本地链接存在；A01–A26 编号各一次；R1 九项均未勾选；代码围栏成对，开发入口与路径已更新 |
| `Get-FileHash -Algorithm SHA256` 前后比较 | 53 个应用、测试、依赖与启动文件内容未变，无删除 |
| `git diff --check` | 通过 |

未执行 pytest、ruff、pip check 或浏览器验收；本轮不把历史通过记录当作新的验证。没有用户计时、性能基准或真实平台核验，体验收益待实施后验证。下一阶段建议仅实施 R1，入口见 CODEX_START_PROMPT；读取模板不自动授权开发。

## P5 提案筛选与分页 · 2026-10-31

- 新增 A21：审核页可组合筛选状态、目标类型和提案/目标 ID；ID 按字面、区分大小写包含匹配，下划线不作为通配符。
- 查询服务在 SQLite 中计数并按创建时间、提案 ID 升序读取，每页最多 50 条；列表、所选记录与审计共用同一读取快照。不新增业务字段或表。
- 切换筛选回到第一页，筛选/页码/记录/数据空间变化会清空未提交的决定；无匹配时移除旧详情及决定/下载入口。列表缩小后收回有效页码，记录决定后显示本地结果提示。
- 浏览器检查发现翻页后下拉框可能残留上一页 ID，已改为随页面上下文重建选择控件；在 Edge 的隔离合成库中复核 101 条提案、第三页单条、ID 筛选回到第一页及空结果提示。筛选区并排显示。截图保存在已忽略的 `local_data/p5-list-page3.png`。
- 当前分页反映每次读取时的数据，不提供跨次翻页的历史快照；并发变更后应重新核对提案 ID。P6 仍需单独授权。验证环境为 Windows、Python 3.14.6。

新增 16 项离线回归，最终验证：

| 实际命令 | 结果 |
| --- | --- |
| `.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider` | 279 passed |
| `.\.venv\Scripts\python.exe -m ruff check .` | All checks passed |
| `.\.venv\Scripts\python.exe -m pip check` | No broken requirements found |
| `git diff --check` | 通过 |
| `.\.venv\Scripts\python.exe -m streamlit run local_data/qa_p5_list.py --server.address 127.0.0.1 --server.port 8502 --server.headless true --browser.gatherUsageStats false` | Edge 浏览器隔离合成验收通过，验收后关闭临时页面并停止该服务 |

隔离验收脚本、合成库和截图位于已忽略的 `local_data/`；未向应用演示库或真实库写入验收提案。浏览器检查使用当前 P5 渲染模块；全应用流程由 Streamlit AppTest 回归覆盖。

## P5 审计完整性加固 · 2026-10-31

- 新增 A20 验收及离线回归，覆盖缺失/重复审计、创建与决定前快照断链、损坏 JSON、错误结构、空决定依据、操作者或时间不一致。
- 本地决定前核对创建审计；已批准待办导出前核对创建、决定前后快照与当前记录。检查与业务读取在同一事务快照中进行，决定失败不写业务或审计。
- 审核页遇到异常记录时显示不含原始内容的错误，停止该提案的决定和下载；其他完整提案仍可处理。不自动修复或删除数据，不改变提案状态机、金额口径或数据库结构。
- 本次环境为 Windows、Python 3.14.6；验证命令及结果见下方。P6 仍需另行授权。

| 实际命令 | 结果 |
| --- | --- |
| `.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests/test_p5_audit_integrity.py --tb=short`（修复前） | 34 failed、3 passed，复现缺口 |
| `.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests/test_p5.py tests/test_p5_audit_integrity.py tests/test_local_revision_time.py --tb=short`（修复后） | 61 passed |
| `.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider`（修复后全量） | 263 passed |
| `.\.venv\Scripts\python.exe -m ruff check .` | All checks passed |
| `.\.venv\Scripts\python.exe -m pip check` | No broken requirements found |
| `git diff --check` | 通过 |

回归关闭 pytest 缓存：初始基线的 226 项测试通过，但既有缓存目录产生 WinError 183 写入警告。本轮未修改该缓存。UI 使用 Streamlit AppTest 验证，未执行人工浏览器验收。审计核对不提供防篡改或来源真实性保证，异常记录保留供人工核对。

## v0.2 2026-10-27

本次优化的是开发方案包，不是应用实现。原 v0.1 文件保持在原始附件中；当前是独立副本。

- **首轮收敛。** 删除首轮全十表、十二笔订单和七页空导航的要求。P0 只验证候选/报价/SKU 与最小经济性；P4 才处理订单。
- **唯一契约。** 新增 DATA_CONTRACT，统一 ID、外键、金额、空值、枚举、时区、费用归属、导入修订和对账；PRD 不再复制字段清单。
- **导入可以落地。** 从“文件 hash 去重”升级为记录 ID + revision + 规范化内容，明确整批预检、确认、事务回滚、错误报告和旧版本拒绝。
- **成本只计一次。** Expense 是唯一成本来源，OrderCost 是派生视图；增加精简分摊与回收/修订规则，不把退款再当费用。
- **净销售与到账分开。** 新增累计结算快照、已覆盖退款、待扣退款和对账差异，说明结算后的新退款如何重算。
- **评分不靠模型拍分。** 新增固定权重、明确档位、证据覆盖率、过期/缺失处理、反驳提示和硬阻断。
- **无 Key 也有草稿。** 保留离线模板，而不是把草稿功能整体锁在模型密钥后面。
- **补齐开发验收。** 按阶段提供空模板、合成示例、确定的金额结果、变更/重复/旧修订等案例。
- **不作平台保证。** 原链接标注未核验；删除没有必要的试验预算建议，保留产品假设与人工控制边界。

### 与 v0.1 CSV 不兼容的变化

v0.1 模板没有稳定记录 ID、revision、统一时区与充分的金额语义，不能直接作为 v0.2 文件导入。请人工对照新模板转换，在预检中确认；不自动猜测旧列含义。特别是旧 `gross_paid` 对应的是新订单的行级总支付额 `paid_amount`，不是单位售价；旧 Expense 与 OrderCost 不能原样同时相加。

### 本包未做

没有创建应用、安装依赖、运行平台接口、验证真实 API 权限、运行应用测试、导入真实业务数据或发布商品。验收案例是下一阶段开发必须实现的目标，不是测试通过证明。
