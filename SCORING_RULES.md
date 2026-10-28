# 可解释选品评分

版本 v0.3 · 2026-10-28 · 规则 `score-v0.3`

这是可调整的产品筛选规则，不是销量预测或平台排名。P2 实现；P1 只展示费用与来源，不在缺少研究时先编一个总分。

## 1 评分输入与有效性

一次计算锁定 product_id、sku_id、quote_id、全部输入 revision、规则版本与 as_of。所有证据必须可追溯，数据契约见 [DATA_CONTRACT](DATA_CONTRACT.md)。示例测试固定 as_of=`2026-10-27T12:00:00+08:00`，真实应用使用用户选定的报告时间。

有效研究必须满足：active 关联、evidence_status=verified、明确许可依据、captured_at 不晚于 as_of 且距 as_of 不超过 90×24 小时。按 dedupe_key 计独立原始材料；同键只计一次。同键同时出现支持与反驳时，该键不计支持并作为反驳展示。context 不计支持分。

有效报价须 quoted_at 不晚于 as_of 且 30×24 小时内；样品 pass/fail 有证据。CandidateCheck 的 pass/fail 要有证据，且 `0 ≤ as_of − checked_at ≤ 90×24小时`。SKU 费用假设须满足 `0 ≤ as_of − assumptions_as_of ≤ 30×24小时`，否则利润维度视为待更新。阈值仅是本版默认假设，不是行业事实。

synthetic 仅在演示库参与评分，并始终显示“合成数据评分，不能据此采购”；真实库不接受 synthetic 研究。

## 2 五维评分

每维取 0–100，再乘固定权重。不因缺失而重新分配权重；缺失为 null，不是 0。

| 维度及权重 | 确定性规则 |
| --- | --- |
| 需求 25% | 有效独立支持证据 n=1 得 40，n=2 得 70，n≥3 得 100；n=0 为 null。有效反驳证据数 ≥ 支持数且大于 0 时，需求分最多 40，并标“证据冲突待复核” |
| 利润 25% | 全部单件费用已知且假设有效：贡献率 ≥35% 得 100；≥25% 得 80；≥15% 得 60；>0 得 30；≤0 得 0。金额缺失或过期为 null |
| 履约 20% | 样品 pass 40/fail 0；支持代发 true 20/false 0；时效 ≤48h 20、49–72h 10、>72h 0；退货条款非空 20。报价过期，或任一字段未知/样品 unknown，整维为 null |
| 内容可展示 15% | visual_demo pass 40、fact_sheet pass 40、media_rights pass 20；fail 对应项得 0。任一 check 未知/过期，整维为 null |
| 销售与声明核验 15% | sales_eligibility pass 50、claims_review pass 50；fail 对应项得 0。任一未知/过期，整维为 null |

```text
total = demand×0.25 + margin×0.25 + fulfillment×0.20
        + content×0.15 + compliance×0.15
```

总分最后 ROUND_HALF_UP 到两位。任一维 null，总分 null；可显示“已核实分项贡献小计”，但不得展示为完整总分。界面显示每个规则输入、证据链接和扣/得分原因；不只显示一个总数。

## 3 先判闸门 再看分数

按以下优先级输出状态：

1. **blocked**：样品 fail；或有效核验中的 sales_eligibility、claims_review、media_rights 任一 fail。展示具体阻断原因。不得因为其他分数高而推荐。
2. **inactive**：候选状态为 archived 或 rejected，或 SKU 状态为 archived；不进入待评审排序。保留计算结果和原因供查看；若同时存在失败阻断，状态仍为 blocked，并显示停用原因。
3. **incomplete**：任一维缺失/过期，或 MOQ/代发是否符合当前场景未知。显示待补项，不参与总分排名。
4. **scenario_mismatch**：min_qty >1 或 dropship_supported=false，不能采用一单一件方案；可保留算术说明但不推荐。
5. **not_viable**：单件贡献利润 ≤0；可保留完整分数但不推荐。
6. **review_required**：存在有效反驳证据；即使分高也先人工复核。
7. **ready_for_manual_review**：其他条件均满足，仅代表可以人工评审，不代表可自动采购或上架。

已记录的失败风险不会随 90 天过期自动解除：过期 fail 继续 blocked，直到同一核验条目有更高 revision 的有证据 pass。样品同理。阈值或排序资格规则更改必须提升规则版本并重算，不能悄悄改历史报告。v0.3 新增本地状态排除规则，其余五维分数及阈值不变。当前系统不回放历史快照。

建议排名只在 ready_for_manual_review 内按 total 降序，同分按证据覆盖率降序、再按 product_id 字典序；其他状态单独分组，不能把 null 排成 0 分尾部混淆。

## 4 证据覆盖率 不是模型自信

显示名称为“证据覆盖率”，不声称预测准确率。固定 10 个证据槽：

- 需求 3 槽：每个有效独立支持证据占 1，最多 3。
- 经济性 1 槽：费用齐全、来源和假设时间有效。
- 履约 1 槽：报价有效且四个履约输入完整有依据（pass/fail 都是已知）。
- 内容核验 3 槽：每个 check 的有效 pass/fail 占 1。
- 销售/声明核验 2 槽：每个 check 的有效 pass/fail 占 1。

`coverage = 已填槽数 / 10 × 100%`。每槽展示满足/缺失原因；fail 会提高覆盖但会阻断，覆盖 100% 不等于质量 100% 或可以销售。过期证据不占槽；重复转载不多占需求槽。不得接受 LLM 自报 confidence 值或用粉赞数替代这些依据。
