"""数据API领域服务：将指标和分析模型发布为对外数据服务API。

真实数据流：定义API接口（关联指标/请求方式/返回格式）
→ 对外暴露指标查询和聚合结果 → 记录每日调用量 → api_endpoints 表。
"""
from __future__ import annotations

import json
from typing import Any

from app.database import db
from app.services.base import BaseModuleService


class DataApiService(BaseModuleService):
    MODULE = "data_api"
    ENTITY = "数据接口"
    OPERATION_TERM = "查询"
    LIST_FIELDS = ["接口名称", "关联指标", "请求方式", "返回格式", "发布状态"]
    REQUIRED_FIELDS = ["接口名称", "关联指标", "请求方式"]
    STATUS_ORDER = ["开发中", "已发布", "已下线"]
    ACTION_RULES = {"发布接口": "已发布", "下线接口": "已下线"}
    NEGATIVE_ACTIONS = []

    def query_metric_api(self, endpoint_id: int) -> dict[str, Any]:
        """通过API接口查询关联指标的最新值。"""
        endpoint = db.fetchone("SELECT * FROM api_endpoints WHERE id=?", [endpoint_id])
        if endpoint is None:
            return {"ok": False, "message": f"接口 {endpoint_id} 不存在"}

        metric_name = endpoint.get("关联指标", endpoint.get("related_metric", ""))
        metric = db.fetchone("SELECT * FROM metrics WHERE metric_name LIKE ?", [f"%{metric_name}%"])

        if not metric:
            return {"ok": False, "message": f"关联指标 {metric_name} 未找到"}

        latest = db.fetchone(
            "SELECT * FROM metric_values WHERE metric_id=? ORDER BY snapshot_time DESC LIMIT 5",
            [metric["id"]],
        ) if metric else None

        # 更新调用计数
        current_calls = endpoint.get("daily_calls", 0) or 0
        db.update("api_endpoints", {"daily_calls": int(current_calls) + 1}, "id=?", [endpoint_id])

        return {
            "ok": True,
            "endpoint": endpoint["endpoint_name"],
            "metric": metric["metric_name"] if metric else metric_name,
            "latest_value": latest["snapshot_value"] if latest else None,
            "history": [{"time": v["snapshot_time"], "value": v["snapshot_value"]}
                       for v in (db.fetchall(
                           "SELECT * FROM metric_values WHERE metric_id=? ORDER BY snapshot_time DESC LIMIT 5",
                           [metric["id"]]))] if metric else [],
        }

    def query_aggregation_api(self, window_name: str) -> dict[str, Any]:
        """通过API查询聚合窗口的最新结果。"""
        window = db.fetchone(
            "SELECT * FROM aggregation_windows WHERE window_name LIKE ?",
            [f"%{window_name}%"],
        )
        if not window:
            return {"ok": False, "message": f"聚合窗口 {window_name} 不存在"}

        results = db.fetchall(
            "SELECT * FROM aggregation_results WHERE window_id=? ORDER BY window_start DESC LIMIT 10",
            [window["id"]],
        )
        return {
            "ok": True,
            "window_name": window["window_name"],
            "results": [{"dim_key": r["dimension_key"], "value": r["computed_value"],
                        "start": r["window_start"]} for r in results],
        }

    def api_usage_stats(self) -> dict[str, Any]:
        """统计API使用情况。"""
        endpoints = db.fetchall("SELECT * FROM api_endpoints WHERE status='已发布'")
        total_calls = sum(int(e.get("daily_calls", 0) or 0) for e in endpoints)
        return {
            "published_endpoints": len(endpoints),
            "total_daily_calls": total_calls,
            "top_endpoints": sorted(
                [{"name": e["endpoint_name"], "calls": e.get("daily_calls", 0)}
                 for e in endpoints],
                key=lambda x: -x["calls"],
            )[:5],
        }

    def domain_snapshot(self) -> dict[str, Any]:
        snapshot = super().domain_snapshot()
        snapshot["total_endpoints"] = db.count("api_endpoints")
        snapshot["published"] = db.count("api_endpoints", "status='已发布'")
        return snapshot