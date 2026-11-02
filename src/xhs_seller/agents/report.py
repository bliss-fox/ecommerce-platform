"""Current-source checks at report generation and navigation boundaries."""

from xhs_seller.agents.runtime import FLOW_VERSION, serialized_view
from xhs_seller.agents.snapshot import current


def require_current(engine, mode, snapshot):
    if not current(engine, mode, snapshot):
        raise ValueError("来源已更新或无法读取，请重新运行；旧结果不能作为当前建议")


def export_report(engine, mode, run):
    require_current(engine, mode, run.snapshot)
    view = run.view()
    if view["status"] not in {"completed", "partial"}:
        raise ValueError("运行未完成，不能导出评估报告")
    return "\n".join(
        [
            "# 离线协作演示 · SKU 评估",
            "",
            "规则与模板模拟，无真实模型。假设不是实际利润；内容预览未保存，不代表可发布。",
            f"流程：{FLOW_VERSION}；运行：{run.run_id}",
            f"空间：{run.snapshot.space}；SKU：{', '.join(run.snapshot.sku_ids)}",
            f"报告时点：{run.snapshot.as_of}；来源读取时点：{run.snapshot.read_at}",
            f"基线：{run.snapshot.baseline}",
            "结果：完整运行（不代表经营通过）"
            if view["status"] == "completed"
            else "结果：部分评估，缺失角色或复核失败；不得作为完整评估。",
            "",
            "以下结构化数据含事实/证据、假设金额、待补项及人工建议。未知保留 null。",
            "",
            "```json",
            serialized_view(view),
            "```",
            "",
            "此文件仅是生成时已核对的本地快照；使用前请在应用重新核对来源。",
            "",
        ]
    )


def navigation_target(engine, mode, run, sku_id):
    require_current(engine, mode, run.snapshot)
    if sku_id not in run.snapshot.sku_ids:
        raise ValueError("目标不属于本次任务")
    return sku_id
