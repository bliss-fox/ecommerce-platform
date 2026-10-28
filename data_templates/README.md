# CSV 模板使用说明

版本 v0.2 · 2026-10-27

这些文件只有表头，没有真实数据。字段、类型与公式的唯一说明在 [DATA_CONTRACT](../DATA_CONTRACT.md)。模板按阶段分目录，不能把提供未来模板理解为首轮需要实现。

| 阶段 | 文件 | 依赖次序 |
| --- | --- | --- |
| P1 | product_candidates、supplier_quotes、skus | 候选 → 报价 → SKU；可在同一原子变更集预检 |
| P2 | research_notes、product_research_links、candidate_checks | 已有候选；研究与关联可一起预检 |
| P4 | orders、expenses、expense_allocations、cost_adjustments、settlements | 已有 SKU；订单 → 费用/分摊 → 调整/结算 |

P4 的 pool 费用与全部分摊必须作为**同一确认批次**提交，不能先写 pool 再暂时留下不平的分摊。初次演示建议一次预检整个 p4 目录。业务导入先检查全部行，再预览并由用户确认，任意错误整批不写。

金额固定 CNY、不带货币符号；允许负数的字段按契约处理；时间必须带时区。空单元格是未知，零要明确写 0.00。不要放真实消费者身份、秘密或未授权材料。旧 v0.1 CSV 必须人工映射后再预检，不自动猜测旧表头。

P0 可以从这些同构示例读取 seed，但不必实现用户 CSV 导入器。没有 OrderCost 模板：订单成本由费用、分摊和调整派生，不能再录入一次。
