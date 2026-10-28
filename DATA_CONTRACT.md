# 数据契约

版本 v0.2 · 2026-10-27 · 契约标识 `xhs-local-cny-v0.2`

本文件是字段、金额、关联、导入和利润口径的唯一真源。CSV 首行与本契约保持一致；模型可使用符合下列约束的内部命名，不得改变含义。P0 只实现第 2 节三类实体和第 3 节经济性；其余随阶段增加。

## 1 全局约定

### 基础类型

| 类型 | 约束 |
| --- | --- |
| ID | 必填字符串，1–64 字符，`[A-Za-z0-9_-]+`，大小写敏感；创建后不可变，用户提供稳定 ID，不按行号生成 |
| revision | 必填正整数，从 1 开始；同 ID 的完整快照版本，非增量金额 |
| updated_at | 必填 ISO 8601 带时区时间，例如 `2026-10-27T08:00:00+08:00`；不接受无时区时间 |
| 时间/日期 | 内部时间统一 UTC，展示 `Asia/Shanghai`；日期 `YYYY-MM-DD` 按上海日历归属；时区不靠机器默认值 |
| money | CSV 为非科学计数法十进制字符串，最多两位小数；固定 CNY，不提供汇率功能。非负，除明确的有符号字段 |
| 金额存储与计算 | 输入先 Decimal 校验，转整数分存储；计算使用 Decimal；拒绝 NaN、Infinity、逗号千分位、¥、多余小数及浮点近似 |
| 舍入 | 外部金额超过两位拒绝，不偷偷舍入；计算出的货币结果最后一步 ROUND_HALF_UP 到分；比例保留内部精度，展示为两位百分数；总额加分值，不先加显示比例 |
| 空值 | CSV 空单元格是 null/未知；`0` 或 `0.00` 是经确认的零；`NULL`、`N/A`、`未知`不当空值解析，报类型错误 |
| bool | 仅 `true`、`false`；允许未知的字段用空值，不能把空值转 false |
| 文本/引用 | UTF-8（允许 BOM），RFC 4180 CSV；去首尾空白，保留正文内部换行；source_ref/evidence_ref 是无秘密的 URL 或本地证据索引，演示使用 `fixture://` |

下文每个可导入实体均有自身 ID、`revision`、`updated_at`，模板实际展开这些列。除标明“可空”外全部必填。所有模板表头固定，禁止未知列；不导入任意 JSON 扩展列。普通备注可空，但空值不影响金额事实。

同一 ID 的更新不允许改变所属主对象：quote.product_id、sku.product_id、研究 link 两端、order 的业务键/sku、expense 的 category/已确定归属、allocation 两端、adjustment 两端、settlement.order_line_id 不得改。误关联需明确纠正流程，不在 CSV 中无声搬迁成本。数量等可修订字段必须重新校验所有依赖。

## 2 P0 与 P1 候选 报价 SKU

### ProductCandidate `p1/product_candidates.csv`

`product_id, revision, updated_at, product_name, category, status, risk_notes`

- status：`researching | shortlisted | rejected | archived`。这是本地选择状态，不是平台上架状态。
- risk_notes 可空；产品名称和类目仅为用户描述，不构成验证结果。

### SupplierQuote `p1/supplier_quotes.csv`

`quote_id, revision, updated_at, product_id, supplier_name, source_ref, quoted_at, unit_cost, shipping_per_order, min_qty, dropship_supported, lead_time_hours, return_terms, sample_result, sample_evidence_ref`

- product_id 外键引用候选；supplier_name 为报价方标签，不在 P0 单建 Supplier 主表。
- quoted_at 为有时区报价时间。unit_cost 是一件采购成本；shipping_per_order 是一单一件的出库运费；均 money，可空。
- min_qty 为 ≥1 整数，可空；dropship_supported 为可空 bool；lead_time_hours 为 ≥0 整数，可空。
- return_terms 可空（未知，不等于“可退”）；sample_result：`unknown | pass | fail`。
- sample_evidence_ref 可空，但 pass/fail 必须提供核验依据；不能由模型自动补齐。
- 报价运费只对应本场景，批量报价、偏远地区加价或多件混单须另做场景，不猜测换算。

### Sku `p1/skus.csv`

`sku_id, revision, updated_at, sku_code, product_id, quote_id, title, sale_price, packaging_per_order, platform_fee_per_order, marketing_per_order, after_sales_loss_per_order, assumption_ref, assumptions_as_of, status`

- sku_code 全库唯一、非空且创建后不可变；product_id/quote_id 必须存在且属于同一候选。
- sale_price 为 money 且 >0，其余四个费用均为 money、可空；全部按一单一件，非整月金额。
- assumption_ref 为假设来源，assumptions_as_of 为有时区时间；只能证明“谁在何时录入了假设”，不是实际费用凭据。
- status：`draft | ready_for_review | archived`；没有 published 或平台同步状态。
- after_sales_loss_per_order 是相对“正常成交基线”的预期增量利润损失，一次性综合考虑预期退款、额外退货费用、可能回收的货品价值和费用返还。不得再另扣同一售后预留；它不是 P4 已发生退款或实际成本。

## 3 P0 与 P1 经济性

固定场景：**一单一件、CNY、同一 SKU 的售价及其他假设**。

```text
non_marketing_cost = unit_cost + shipping_per_order + packaging_per_order
                     + platform_fee_per_order + after_sales_loss_per_order
break_even_cac = sale_price - non_marketing_cost
estimated_contribution = break_even_cac - marketing_per_order
estimated_margin = estimated_contribution / sale_price
```

贡献利润和贡献率只有全部六项费用（采购、发货、包装、平台、获客、售后损失）已知时才可计算。保本 CAC 不依赖 marketing_per_order，因此仅获客费未知时仍可单独计算；标为“最多可承受获客费用的估计值”，负数不截断成 0。已知成本小计可以展示，但不称为总成本。

所有结果都是假设估算，不是实际订单利润。选中报价的 MOQ/代发不符合一单一件场景时，保留可见的算术结果并加“场景不匹配，不可据此采用”标识，不进入可推荐排序。报价选择变化需记录 SKU 修订。

## 4 P2 研究与证据

### ResearchNote `p2/research_notes.csv`

`research_id, revision, updated_at, keyword, source_ref, captured_at, published_at, pain_point, evidence_summary, evidence_status, permission_basis, dedupe_key, evidence_ref`

- captured_at 有时区；published_at 可空，存在时不得晚于 captured_at。
- evidence_status：`unverified | verified | rejected`；verified 必须有 evidence_ref 与明确 permission_basis。
- permission_basis：`manual_owned | authorized_export | synthetic | unknown`；unknown 不能评 verified。synthetic 仅限演示数据库。
- dedupe_key 为同一原始材料/转述链的稳定标识；可重复，重复时在评分中只算一个独立证据。
- pain_point、evidence_summary 是记录内容，不等于市场总体需求。没有 confidence 输入列，置信提示由评分规则计算。

### ProductResearchLink `p2/product_research_links.csv`

`link_id, revision, updated_at, product_id, research_id, relationship, active`

- 两个外键必须存在；`(product_id, research_id)` 全库唯一。
- relationship：`supports | contradicts | context`；active 为 bool，撤销关联使用 false，保留历史。

### CandidateCheck `p2/candidate_checks.csv`

`check_id, revision, updated_at, product_id, check_type, result, checked_at, evidence_ref, note`

- `(product_id, check_type)` 全库唯一。
- check_type：`visual_demo | fact_sheet | media_rights | sales_eligibility | claims_review`。
- result：`pass | fail | unknown`；checked_at 和 evidence_ref 在 pass/fail 时必填，unknown 可空；note 可空。
- sales_eligibility 是用户对销售条件的人工核验结果，不是软件提供的法律意见；unknown 不能推断为允许。

评分是派生输出，不允许 CSV 直接写总分或“AI 信心”。完整算法见 [评分规则](SCORING_RULES.md)。

## 5 P4 订单与成本 单一来源

### OrderLine `p4/orders.csv`

`order_line_id, revision, updated_at, store_id, order_id, line_item_id, sku_id, quantity, paid_amount, refunded_amount, returned_units, status, paid_at, refund_updated_at`

- `(store_id, order_id, line_item_id)` 唯一，和稳定 order_line_id 一一对应；不能换新 ID 重复导入同一订单行。
- quantity 是 ≥1 的整数；所有金额都是**整条订单行总额**，不是单位售价，不再乘 quantity。
- paid_amount 是折扣/商家优惠已分摊后实际支付给该订单行的商品金额，非挂牌 GMV；不包括本版未建模的独立运费收入、平台补贴、税费或跨币种项目。若源数据混有这些项，预检阻止并要求人工明确映射，不能静默忽略。
- refunded_amount 是累计已实际退款金额，`0 ≤ refunded_amount ≤ paid_amount`；待申请退款不计入。允许仅部分金额退款而不退货。
- returned_units 是实际退回数量，整数 `0..quantity`；收到货不等于价值已回收。refund_updated_at 在 refunded_amount > 0 时必填。
- status：`paid | shipped | completed | cancelled`，不是结算状态；paid_at 为有时区时间。
- P4 无消费者身份信息，store/order/line 都使用可稳定回查的去身份化键，不包含手机号、地址等。

### Expense `p4/expenses.csv` 成本唯一录入源

`expense_id, revision, updated_at, expense_date, category, amount, basis, allocation_mode, order_line_id, source_ref, remark`

- category：`goods | outbound_shipping | return_shipping | packaging | platform_fee | marketing | other | overhead`。
- 前七类为订单变量成本；goods 是本行发出商品的原始采购总成本，先不扣回收；overhead 为固定经营费用，只单列，不混入 SKU 贡献利润。
- basis：`actual | estimated | unknown`；actual/estimated 必须有非负金额，unknown 的 amount 必须为空。显式 0 也必须有依据，表示该类无费用。
- allocation_mode：`direct | pool | unallocated | overhead`。
  - direct：order_line_id 必填，无分摊行；amount 全额归此订单行。
  - pool：order_line_id 必须空；已知金额通过 ExpenseAllocation 全部分摊。
  - unallocated：order_line_id 必须空、无分摊行、金额已知；留待以后确认归属，不计入任意订单利润。
  - overhead：category 必须 overhead，order_line_id 空、无分摊行；其他模式禁止 category=overhead。
- unallocated 可以用同 ID 更高 revision 转为 direct 或 pool，并在同批提供全部归属数据；一旦已归属，不能通过 CSV 无声迁移到另一订单。
- unknown 仅允许 direct。source_ref 是该成本事项唯一的证据索引，在 Expense 与 CostAdjustment 之间全库唯一；同张凭据若跨订单，建立 pool，不复制 direct 记录；若同一凭据包含不同费用类别，可使用该凭据的可追溯子项索引（例如本地凭据编号加行号），不同子项确实对应不同金额，不能编造新的证据或复制同一子项。
- 本版采用**每订单行每成本类别一个归属来源**：direct 或一个 pool allocation 二选一。多张同类凭据先人工汇总成一个来源包；额外差额用修订/调整，不并存两个基础来源。本版不做凭据级全量 ERP。
- 估计转实际或更正金额：更新同 expense_id 的更高 revision，不另插入一笔“实际费用”与原估计相加。

### ExpenseAllocation `p4/expense_allocations.csv`

`allocation_id, revision, updated_at, expense_id, order_line_id, amount`

- expense_id 必须引用 pool，amount 非负；`(expense_id, order_line_id)` 唯一。
- 同一 pool 至少有一条订单分摊，全部分摊之和必须**精确等于** expense.amount；即使金额为零，也需明确关联至少一条 0 元分摊；pool、分摊和相关修订在一次原子导入里验证。少分、超分均拒绝，未决定归属时用 unallocated 模式。
- 分摊继承 Expense 的 category、basis、日期与证据，不新增一次费用。某订单行该类别已有 direct 或其他 pool 时拒绝。
- 默认不自动分摊。用户选择按数量分摊时，以整数分按权重先向下取整，余分按小数余数从大到小分配，同余时按 order_line_id 字典序；提交前展示并确认。其他方法只能人工提供金额并写 remark。

### CostAdjustment `p4/cost_adjustments.csv`

`adjustment_id, revision, updated_at, expense_id, order_line_id, kind, amount, basis, occurred_at, recovered_units, source_ref, reason`

- 引用已分配到该行的 Expense，继承其 category。kind：`correction | recovery`；amount 为有符号 money，basis：`actual | estimated`。
- correction 为有凭据的后续成本加减，例如运费补收、费用返还；recovered_units 必须空。原数据录错或估计转实际优先修订原 Expense，不再插入差额重复计账。
- recovery 只允许 goods 类、amount ≤0、recovered_units 为 ≥1 整数。所有当前 recovery 的 recovered_units 之和 ≤订单 returned_units；不对尚未回收的退货默认冲减成本。
- 每次回收绝对金额不得超过 `原始归属 goods 金额 × 本次回收件数 / quantity`，ROUND_HALF_UP 到分；全部回收总额也不得超过原始 goods 金额。全额可再售的样例按历史采购成本回收；折损回收须有 reason/检查依据，并可取更低值，不使用预期售价。
- 同行同类基础成本加所有当前调整后不得小于 0；未知基础成本不能靠调整变成已知利润。
- 每条 adjustment 是独立事项，其 revision 仍是该事项的完整替换快照，不能把修订金额再累加。退款金额禁止录入 correction 或 other。

### OrderCost 只读派生视图

实现可以计算视图或查询结果，**不提供第二个 OrderCost CSV 或可写费用表**。

```text
某行某类成本 = direct Expense.amount 或对应 pool allocation.amount
              + 此来源此订单行的全部当前 CostAdjustment.amount
```

七个变量成本类别必须各有一个基础归属；“无此费用”以 basis=actual、amount=0 明示。任何类别没有来源或 basis=unknown → 整行贡献利润为 null；有 estimated → 显示估计；全部 actual 且调整也 actual → “按当前已知实际成本计算”。只要有未知，其他已知类别仍可展示小计。

汇总 SKU 时只加派生订单成本，**不得再次扣全部 Expense**。店铺核对显示“已归属费用 + 未分摊费用 + overhead”，用来解释费用覆盖，不把它再次减到 SKU 贡献中。存在相关期间未分摊变量费用时，汇总必须标为“费用未分配完，非完整经营结果”。

## 6 P4 净销售 利润 与结算

```text
net_sales = paid_amount - refunded_amount
variable_cost = 七类派生成本之和
contribution = net_sales - variable_cost
contribution_margin = contribution / net_sales  # net_sales=0 时为 null
```

退款只在 net_sales 扣一次；商品回收只减少 goods 成本，不能再作为一笔销售收入。P1 售后预留不能混入 P4 实际成本；未来的预计损失另列情景，不与已发生退款相加。所有订单行成本/金额都是总额，不再乘 quantity。

`contribution` 不是净利润、税后利润或现金流。即使成本全部实际确认也可能以后修订；报告必须标 as_of 和输入 revision，不称为永久最终收益。

### SettlementSnapshot `p4/settlements.csv`

`settlement_id, revision, updated_at, order_line_id, cutoff_at, covered_refunded_amount, platform_fee_deducted, other_withheld, settlement_adjustments, actual_received, source_ref`

- 本版只接受整条订单行本金已进入该次结算范围的快照，不支持只结算部分本金或分批放款。源数据属于部分本金结算时预检明确报不支持并请求人工拆解/后续扩展，不能误报成普通对账差异；尚未结算的行不创建快照。
- 每订单行唯一 settlement_id；同 ID 高 revision 表示更新的**累计快照**，不能把多个修订到账额相加。cutoff_at 单调不减，不能早于订单 paid_at，也不得晚于 updated_at。
- covered_refunded_amount 为截至 cutoff 已在结算中覆盖的累计退款，`0..当前 order.refunded_amount`。
- platform_fee_deducted、other_withheld 为累计现金扣款，均非负；settlement_adjustments、actual_received 允许正负，必须有来源。它们是结算核对项，不自动新增经营费用或收入。
- other_withheld 是暂扣/其他现金项，未明确其经济性质前不能当成本；需要确认费用时另外关联唯一 Expense 证据，避免重复。
- 所有快照金额必填；没有结算证据就不创建空壳快照，显示“未记录结算”。

```text
expected_received_at_cutoff = paid_amount - covered_refunded_amount
                              - platform_fee_deducted - other_withheld
                              + settlement_adjustments
reconciliation_difference = actual_received - expected_received_at_cutoff
refund_not_yet_covered = current_refunded_amount - covered_refunded_amount
```

差异不为 0.00 就待核对，不自动凑零，也不改利润来匹配到账。平台费可能扣款与费用确认时点不同，两个金额差异另作解释，不在利润里重复扣 platform_fee_deducted。

**结算后退款：** 当前 net_sales 和利润立即随新的订单修订重算；旧快照仍按其 covered_refunded_amount 核对，保持已记录实际到账不变。refund_not_yet_covered >0 时明确标“退款尚未反映在结算”；收到新的累计结算再修订同 settlement_id。旧快照差异为 0 不代表当前退款已结清。

报表至少分开：净销售额、已知/估计/未知成本、贡献利润、已记录累计到账、待反映退款、结算差异。不得用 actual_received 减一次平台费后再称 net_sales。

## 7 导入协议 P1 起共用

### 预检与提交

1. 用户选择契约版本和文件类型；不根据相似列名静默猜测。UTF-8 解码、重复/未知/缺失列、类型、枚举、PII/凭证风险先检查。
2. 把选定的一组文件当成一个候选变更集，解析全部行。全部关联与金额约束在“现存数据 + 候选快照”上校验，可跨同批文件引用。
3. 生成预检摘要：新增、更新、不变、拒绝的行数，金额变化、缺失费用、警告，以及每行错误。任何错误时**整批不能确认**，不静默导入好行。
4. 用户看预览并明确确认后，使用一个数据库事务提交业务记录与成功审计。提交前检查预检基线版本未变；变化则返回重新预检。任意写失败全部回滚。
5. 错误报告可本地导出，包含 file、row_number（CSV 记录的物理起始行，表头为第 1 行）、record_id、field、error_code、message；多行字段按记录起始行定位，不回显原行或敏感值。失败批次元数据可单独记录，不保留被拒绝的敏感内容。

表内同一 ID 重复出现，即使内容相同也拒绝为 `DUPLICATE_ID_IN_BATCH`；冲突业务键拒绝。文件 hash 可以识别同一上传内容，但**不能替代记录级检查**。

### 幂等与修订

- 新 ID：插入；所有外键、唯一键和业务约束必须通过。
- 已有 ID、相同 revision、规范化业务内容相同：no-op。文件名、行序、列序不影响；金额 `1` 与 `1.00`、等价时区时间视为相同。
- 已有 ID、相同 revision、任意业务内容不同：`REVISION_CONFLICT`，整批拒绝。
- 更高 revision：updated_at 必须严格晚于现存版本，验证后替换当前快照并追加旧/新修订审计；不能把金额当新增增量。允许跳号但展示警告。
- 更低 revision：`STALE_REVISION`，整批拒绝；不能覆盖新退款、新结算或实际费用，也不能靠旧文件 hash 绕过检查。
- 新 ID 但业务唯一键已存在：`BUSINESS_KEY_CONFLICT`，不能当新业务记录。
- CSV 不删除记录；候选/SKU 归档、研究关联停用、金额纠正都保留历史。成本事项取消可用更高 revision 的显式 0 并记录原因，仍须满足关联约束。

规范化 hash 包括 ID、revision、所有业务字段与 updated_at，排除文件名/行号；时间先统一 UTC、金额先转分、bool 标准化、空值统一 null，字段按契约排序。原文件字节 hash 另存。错误、no-op 和成功均有批次摘要；全 no-op 不重复写业务修订。

最小审计元数据包括：batch_id、契约版本、文件名称及 SHA256、导入时间、行数及分类结果、操作者本地标识、确认时间；修订日志记录 entity_type、record_id、old/new revision、规范化 old/new 值与 batch_id。P0 不要求实现这些导入表。

推荐错误码：`SCHEMA_ERROR`、`TYPE_ERROR`、`PRIVACY_RISK`、`FK_NOT_FOUND`、`DUPLICATE_ID_IN_BATCH`、`BUSINESS_KEY_CONFLICT`、`REVISION_CONFLICT`、`STALE_REVISION`、`TIME_ORDER_ERROR`、`ALLOCATION_MISMATCH`、`DOUBLE_COST_SOURCE`、`RECOVERY_LIMIT`、`BASELINE_CHANGED`。不合法币种/补贴混合源文件属于 SCHEMA_ERROR，不自动换算。

## 8 P3 与 P5 后续内部实体

这些实体不在当前 CSV 交换范围，随对应阶段开发，仍遵守 ID、revision、时间、引用和审计约束。

- ContentDraft：draft_id、sku_id、已核实事实列表（事实文本/证据索引/核验时间）、素材索引列表（本地路径/权利依据）、title、body、shot_list、needs_verification、status=`draft | reviewed | rejected`、created_at。每条写成事实的句子须可追溯；reviewed 也不等于已发布。素材默认不发送外部模型。
- ActionProposal：proposal_id、action_type=`list | reprice | purchase | refund | advertise | other`、target_type/target_id、before/after 快照、reason、evidence_refs、estimated_cost（可空 money）、status=`proposed | approved | rejected | cancelled | expired`、created_at、decided_at/decided_by（决定后必填）。只能 proposed 转其他终态；修改终态意图需新提案。批准无执行权限、无平台写入接口。

P5 本地实现限定目标为已有 product、sku 或 order；前后快照为人工录入文本，证据索引至少一项。预计成本空白为未知，零须明确输入。到期由人工标记，没有自动到期时间。每次创建及终态决定与前后修订审计原子写入；终态不可改，批准仅允许导出人工待办。复盘使用 P4 当前修订，不保存历史快照。
