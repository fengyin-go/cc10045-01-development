"""留存分析领域服务：计算用户或业务对象的留存率并支持多维拆解。

真实数据流：定义留存任务（行为事件/留存天数/分群维度）
→ pipeline.compute_retention 从 events 表计算 cohort 留存矩阵
→ 写入 retention_results → 下游报告和仪表盘消费。
"""
from __future__ import annotations

from typing import Any

from app.database import db
from app.pipeline import compute_retention
from app.services.base import BaseModuleService


class RetentionAnalysisService(BaseModuleService):
    MODULE = "retention_analysis"
    ENTITY = "留存任务"
    OPERATION_TERM = "计算"
    LIST_FIELDS = ["任务名称", "行为事件", "留存天数", "分群维度", "最近结果"]
    REQUIRED_FIELDS = ["任务名称", "行为事件", "留存天数"]
    STATUS_ORDER = ["待计算", "计算中", "已完成", "计算失败"]
    ACTION_RULES = {"启动计算": "已完成", "取消计算": "计算失败"}
    NEGATIVE_ACTIONS = []

    def run_computation(self, task_id: int) -> dict[str, Any]:
        """执行留存计算。"""
        task = db.fetchone("SELECT * FROM retention_tasks WHERE id=?", [task_id])
        if task is None:
            return {"ok": False, "message": f"留存任务 {task_id} 不存在"}

        db.update("retention_tasks", {"status": "计算中"}, "id=?", [task_id])
        results = compute_retention(dict(task))
        db.update("retention_tasks", {"status": "已完成"}, "id=?", [task_id])

        day0_rate = 1.0
        if results:
            day0_rows = [r for r in results if r["day_index"] == 0]
            if day0_rows:
                day0_rate = day0_rows[0]["retention_rate"]
            last_retention = results[-1]["retention_rate"] if results else 0
        else:
            last_retention = 0

        return {
            "ok": True,
            "task_name": task["task_name"],
            "day0_retention": day0_rate,
            "final_retention": last_retention,
            "results": results,
        }

    def retention_curve(self, task_id: int) -> dict[str, Any]:
        """获取留存曲线数据。"""
        rows = db.fetchall(
            "SELECT * FROM retention_results WHERE task_id=? ORDER BY day_index ASC",
            [task_id],
        )
        if not rows:
            return {"curve": [], "message": "无数据"}

        curve = [{"day": r["day_index"], "rate": r["retention_rate"],
                  "cohort_size": r["cohort_size"], "retained": r["retained_count"]} for r in rows]

        return {
            "curve": curve,
            "day0_rate": curve[0]["rate"] if curve else 0,
            "day7_rate": curve[6]["rate"] if len(curve) > 6 else None,
        }

    def domain_snapshot(self) -> dict[str, Any]:
        snapshot = super().domain_snapshot()
        snapshot["total_tasks"] = db.count("retention_tasks")
        snapshot["total_results"] = db.count("retention_results")
        return snapshot