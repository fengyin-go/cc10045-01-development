"""预测模型领域服务：基于历史指标数据执行趋势和时间序列预测。

真实数据流：定义预测任务（指标/周期/方法/基线天数）
→ pipeline.compute_forecast 执行 SES/移动平均/线性趋势预测
→ 写入 forecast_results → 下游报告和告警消费者。
"""
from __future__ import annotations

from typing import Any

from app.database import db
from app.pipeline import compute_forecast
from app.services.base import BaseModuleService


class ForecastModelService(BaseModuleService):
    MODULE = "forecast_model"
    ENTITY = "预测任务"
    OPERATION_TERM = "预测"
    LIST_FIELDS = ["任务名称", "预测指标", "预测周期", "预测方式", "历史基线天数"]
    REQUIRED_FIELDS = ["任务名称", "预测指标", "预测周期"]
    STATUS_ORDER = ["待执行", "执行中", "已完成", "执行失败"]
    ACTION_RULES = {"启动预测": "已完成", "取消预测": "执行失败"}
    NEGATIVE_ACTIONS = []

    def run_forecast(self, task_id: int) -> dict[str, Any]:
        """执行预测任务。"""
        task = db.fetchone("SELECT * FROM forecast_tasks WHERE id=?", [task_id])
        if task is None:
            return {"ok": False, "message": f"预测任务 {task_id} 不存在"}

        db.update("forecast_tasks", {"status": "执行中"}, "id=?", [task_id])
        results = compute_forecast(dict(task))
        db.update("forecast_tasks", {"status": "已完成"}, "id=?", [task_id])

        return {
            "ok": True,
            "task_name": task["task_name"],
            "method": task["forecast_method"],
            "forecasts": results,
        }

    def forecast_data(self, task_id: int) -> list[dict[str, Any]]:
        """获取预测结果。"""
        return db.fetchall(
            "SELECT * FROM forecast_results WHERE task_id=? ORDER BY forecast_date ASC",
            [task_id],
        )

    def evaluate_accuracy(self, task_id: int) -> dict[str, Any]:
        """评估预测准确率：对比预测值与实际值。"""
        task = db.fetchone("SELECT * FROM forecast_tasks WHERE id=?", [task_id])
        if task is None:
            return {"ok": False, "message": "任务不存在"}

        forecasts = db.fetchall(
            "SELECT * FROM forecast_results WHERE task_id=? ORDER BY forecast_date ASC LIMIT 7",
            [task_id],
        )

        metric = db.fetchone(
            "SELECT id FROM metrics WHERE metric_name LIKE ?",
            [f"%{task['target_metric']}%"],
        )
        if not metric:
            return {"ok": False, "message": "关联指标不存在"}

        actuals = db.fetchall(
            "SELECT snapshot_value, snapshot_time FROM metric_values WHERE metric_id=? ORDER BY snapshot_time DESC LIMIT 7",
            [metric["id"]],
        )

        errors = []
        for f, a in zip(forecasts, reversed(actuals)):
            err = abs(f["predicted_value"] - a["snapshot_value"]) / max(abs(a["snapshot_value"]), 0.0001)
            errors.append(err)

        mape = sum(errors) / max(len(errors), 1) * 100

        return {
            "ok": True,
            "task_name": task["task_name"],
            "mape_pct": round(mape, 2),
            "accuracy_rating": "good" if mape < 10 else ("acceptable" if mape < 20 else "poor"),
            "comparisons": len(errors),
        }

    def domain_snapshot(self) -> dict[str, Any]:
        snapshot = super().domain_snapshot()
        snapshot["total_tasks"] = db.count("forecast_tasks")
        snapshot["total_forecasts"] = db.count("forecast_results")
        return snapshot