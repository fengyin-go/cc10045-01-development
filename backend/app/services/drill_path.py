"""下钻路径领域服务：定义从汇总到明细的多层下钻分析路径。

真实数据流：定义下钻规则（起始指标/下钻维度/跳转目标/默认粒度）
→ 执行下钻：从 aggregation_results 读取汇总数据 → 按维度展开明细
→ 跳转到目标模块的实体详情。
"""
from __future__ import annotations

from typing import Any

from app.database import db
from app.services.base import BaseModuleService


class DrillPathService(BaseModuleService):
    MODULE = "drill_path"
    ENTITY = "下钻规则"
    OPERATION_TERM = "查询"
    LIST_FIELDS = ["规则名称", "起始指标", "下钻维度", "跳转目标", "默认粒度"]
    REQUIRED_FIELDS = ["规则名称", "起始指标", "下钻维度"]
    STATUS_ORDER = ["草稿", "已启用", "已停用"]
    ACTION_RULES = {"启用规则": "已启用", "停用规则": "已停用"}
    NEGATIVE_ACTIONS = []

    def execute_drill(self, rule_id: int) -> dict[str, Any]:
        """执行下钻：从汇总指标逐层展开到明细数据。"""
        rule = db.fetchone("SELECT * FROM drill_rules WHERE id=?", [rule_id])
        if rule is None:
            return {"ok": False, "message": f"下钻规则 {rule_id} 不存在"}

        start_metric = rule.get("起始指标", rule.get("start_metric", ""))
        drill_dim = rule.get("下钻维度", rule.get("drill_dimension", ""))
        target = rule.get("跳转目标", rule.get("target_module", ""))
        granularity = rule.get("默认粒度", rule.get("default_granularity", "day"))

        layers: list[dict[str, Any]] = []

        # Layer 1: 起始指标汇总值
        metric = db.fetchone("SELECT * FROM metrics WHERE metric_name LIKE ?", [f"%{start_metric}%"])
        if metric:
            values = db.fetchall(
                "SELECT * FROM metric_values WHERE metric_id=? ORDER BY snapshot_time DESC LIMIT 5",
                [metric["id"]],
            )
            layers.append({
                "layer": 1,
                "name": f"指标: {start_metric}",
                "data": [{"time": v["snapshot_time"], "value": v["snapshot_value"]} for v in values],
                "granularity": granularity,
            })
        else:
            layers.append({"layer": 1, "name": start_metric, "data": [], "granularity": granularity})

        # Layer 2: 按下钻维度拆解聚合结果
        agg_results = db.fetchall(
            "SELECT * FROM aggregation_results WHERE dimension_key LIKE ? ORDER BY window_start DESC LIMIT 30",
            [f"%{drill_dim}%"],
        )
        layers.append({
            "layer": 2,
            "name": f"按{dill_dim}拆解",
            "data": [{"dim_key": r["dimension_key"], "value": r["computed_value"],
                     "event_count": r["event_count"]} for r in agg_results],
            "granularity": granularity,
        })

        # Layer 3: 跳转到目标模块的实体详情
        if target == "events" or target == "event_tracker":
            target_events = db.fetchall(
                "SELECT * FROM events WHERE event_attrs LIKE ? LIMIT 20",
                [f"%{drill_dim}%"],
            )
            layers.append({
                "layer": 3,
                "name": f"明细事件: {target}",
                "data": [{"event_name": e["event_name"], "occurred_at": e["occurred_at"],
                         "attrs": e["event_attrs"]} for e in target_events],
                "granularity": "raw",
            })
        elif target:
            target_data = db.fetchall(f"SELECT * FROM {target} LIMIT 20") if target else []
            layers.append({
                "layer": 3,
                "name": f"跳转目标: {target}",
                "data": target_data,
                "granularity": "raw",
            })

        return {
            "ok": True,
            "rule_name": rule.get("规则名称", rule.get("rule_name", "")),
            "start_metric": start_metric,
            "drill_dimension": drill_dim,
            "layers": layers,
        }

    def domain_snapshot(self) -> dict[str, Any]:
        snapshot = super().domain_snapshot()
        snapshot["total_rules"] = db.count("drill_rules")
        snapshot["enabled_rules"] = db.count("drill_rules", "status='已启用'")
        return snapshot