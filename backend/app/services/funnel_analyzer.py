"""漏斗分析器领域服务：定义多步骤行为漏斗并计算各步转化率。

真实数据流：输入漏斗步骤序列 → pipeline.compute_funnel 从 events 表逐层过滤
→ 计算每步进入/离开/转化率 → 写入 funnel_results → 下游报告和API消费。
"""
from __future__ import annotations

import json
from typing import Any

from app.database import db
from app.pipeline import compute_funnel
from app.services.base import BaseModuleService


class FunnelAnalyzerService(BaseModuleService):
    MODULE = "funnel_analyzer"
    ENTITY = "漏斗定义"
    OPERATION_TERM = "计算"
    LIST_FIELDS = ["漏斗名称", "步骤序列", "转化窗口小时", "分群条件", "最近转化率"]
    REQUIRED_FIELDS = ["漏斗名称", "步骤序列", "转化窗口小时"]
    STATUS_ORDER = ["草稿", "已启用", "已停用"]
    ACTION_RULES = {"启用漏斗": "已启用", "停用漏斗": "已停用"}
    NEGATIVE_ACTIONS = []

    def run_computation(self, funnel_id: int) -> dict[str, Any]:
        """执行漏斗分析计算。"""
        funnel = db.fetchone("SELECT * FROM funnels WHERE id=?", [funnel_id])
        if funnel is None:
            return {"ok": False, "message": f"漏斗 {funnel_id} 不存在"}

        results = compute_funnel(dict(funnel))

        overall_rate = results[-1]["conversion_rate"] if results else 0
        return {
            "ok": True,
            "funnel_name": funnel["funnel_name"],
            "steps": len(results),
            "overall_conversion_rate": overall_rate,
            "steps_detail": results,
        }

    def funnel_results(self, funnel_id: int) -> list[dict[str, Any]]:
        """获取漏斗计算结果。"""
        return db.fetchall(
            "SELECT * FROM funnel_results WHERE funnel_id=? ORDER BY step_index ASC",
            [funnel_id],
        )

    def compare_funnels(self, funnel_id_a: int, funnel_id_b: int) -> dict[str, Any]:
        """对比两个漏斗的转化率差异。"""
        results_a = self.run_computation(funnel_id_a)
        results_b = self.run_computation(funnel_id_b)

        funnel_a = db.fetchone("SELECT funnel_name FROM funnels WHERE id=?", [funnel_id_a])
        funnel_b = db.fetchone("SELECT funnel_name FROM funnels WHERE id=?", [funnel_id_b])

        steps_a = results_a.get("steps_detail", [])
        steps_b = results_b.get("steps_detail", [])

        comparison = []
        for sa, sb in zip(steps_a, steps_b):
            diff = round(sa["conversion_rate"] - sb["conversion_rate"], 4)
            comparison.append({
                "step": sa["step_index"],
                "rate_a": sa["conversion_rate"],
                "rate_b": sb["conversion_rate"],
                "diff": diff,
            })

        return {
            "funnel_a": funnel_a["funnel_name"] if funnel_a else "?",
            "funnel_b": funnel_b["funnel_name"] if funnel_b else "?",
            "overall_diff": round(
                results_a.get("overall_conversion_rate", 0) - results_b.get("overall_conversion_rate", 0), 4),
            "comparison": comparison,
        }

    def drop_analysis(self, funnel_id: int) -> dict[str, Any]:
        """分析漏斗各步骤的流失原因。"""
        rows = db.fetchall(
            "SELECT * FROM funnel_results WHERE funnel_id=? ORDER BY step_index ASC",
            [funnel_id],
        )
        max_drop_step = None
        max_drop_count = 0
        step_analysis = []

        for r in rows:
            if r["step_index"] > 0:
                drop = r["entry_count"] - r["exit_count"]
                drop_reasons = {}
                try:
                    drop_reasons = json.loads(r.get("drop_reasons", "{}"))
                except (json.JSONDecodeError, TypeError):
                    pass
                step_analysis.append({
                    "step": r["step_name"],
                    "entry": r["entry_count"],
                    "exit": r["exit_count"],
                    "drop": drop,
                    "drop_rate": round(drop / max(r["entry_count"], 1), 4),
                    "reasons": drop_reasons,
                })
                if drop > max_drop_count:
                    max_drop_count = drop
                    max_drop_step = r["step_name"]

        return {
            "funnel_id": funnel_id,
            "steps": step_analysis,
            "max_drop_step": max_drop_step,
            "max_drop_count": max_drop_count,
        }

    def domain_snapshot(self) -> dict[str, Any]:
        snapshot = super().domain_snapshot()
        snapshot["total_funnels"] = db.count("funnels")
        snapshot["total_results"] = db.count("funnel_results")
        return snapshot