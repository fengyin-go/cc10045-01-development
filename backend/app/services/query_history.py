"""查询历史领域服务：记录和审计所有分析查询的调用链路和性能。

真实数据流：每次分析查询调用 → 记录查询时间/执行人/涉及数据集/查询摘要/返回行数/耗时
→ 写入 query_log 表 → 自动标记慢查询（> 500ms）和超时（> 30s）。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.database import db
from app.services.base import BaseModuleService


class QueryHistoryService(BaseModuleService):
    MODULE = "query_history"
    ENTITY = "查询记录"
    OPERATION_TERM = "查询"
    LIST_FIELDS = ["查询时间", "执行人", "涉及数据集", "查询语句摘要", "返回行数"]
    REQUIRED_FIELDS = ["查询时间", "执行人", "涉及数据集"]
    STATUS_ORDER = ["正常", "慢查询", "超时终止"]
    ACTION_RULES = {"标记慢查询": "慢查询", "重新执行": "正常"}
    NEGATIVE_ACTIONS = []

    def record_query(
        self, dataset: str, query_summary: str, row_count: int,
        execution_time_ms: int, executor: str = "system",
    ) -> dict[str, Any]:
        """记录一条查询日志。"""
        status = "正常"
        if execution_time_ms > 30000:
            status = "超时终止"
        elif execution_time_ms > 500:
            status = "慢查询"

        log_id = db.insert("query_log", {
            "executor": executor,
            "dataset": dataset,
            "query_summary": query_summary,
            "row_count": row_count,
            "execution_time_ms": execution_time_ms,
            "status": status,
        })
        return {"id": log_id, "status": status}

    def performance_summary(self, hours: int = 24) -> dict[str, Any]:
        """获取近期查询性能摘要。"""
        total = db.count("query_log")
        slow = db.count("query_log", "status='慢查询'")
        timeout = db.count("query_log", "status='超时终止'")
        normal = db.count("query_log", "status='正常'")

        # 平均耗时
        avg_row = db.fetchone("SELECT AVG(execution_time_ms) as avg_ms FROM query_log")
        avg_ms = round(float(avg_row["avg_ms"]), 2) if avg_row and avg_row["avg_ms"] else 0

        return {
            "total_queries": total,
            "normal": normal,
            "slow_queries": slow,
            "timeout_queries": timeout,
            "avg_execution_ms": avg_ms,
            "slow_rate": round(slow / max(total, 1), 4),
        }

    def top_slow_queries(self, limit: int = 10) -> list[dict[str, Any]]:
        """获取最慢的查询记录。"""
        return db.fetchall(
            "SELECT * FROM query_log WHERE status IN ('慢查询','超时终止') ORDER BY execution_time_ms DESC LIMIT ?",
            [limit],
        )

    def dataset_usage(self) -> list[dict[str, Any]]:
        """统计各数据集的查询频率。"""
        rows = db.fetchall(
            "SELECT dataset, COUNT(*) as total_queries, AVG(execution_time_ms) as avg_ms "
            "FROM query_log GROUP BY dataset ORDER BY total_queries DESC",
        )
        return rows

    def domain_snapshot(self) -> dict[str, Any]:
        snapshot = super().domain_snapshot()
        snapshot["total_queries"] = db.count("query_log")
        snapshot["slow_count"] = db.count("query_log", "status='慢查询'")
        snapshot["timeout_count"] = db.count("query_log", "status='超时终止'")
        return snapshot