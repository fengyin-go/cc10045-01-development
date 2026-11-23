"""实时聚合器领域服务：管理聚合窗口配置并驱动真实聚合计算。

真实数据流：接收事件定义 → 按窗口维度分组 → 执行 COUNT/SUM/AVG/MAX/MIN 等聚合函数
→ 结果写入 aggregation_results 表 → 供下游指标存储和仪表盘消费。

窗口类型支持 tumbling（滚动）、sliding（滑动）、session（会话）三种，
每种类型有独立的时间语义和结果合并策略。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from app.database import db
from app.pipeline import compute_aggregation, next_computation_version
from app.services.base import BaseModuleService


def _window_dims(window: dict[str, Any]) -> list[str]:
    import json
    raw = window.get("dimensions", "[]")
    try:
        dims = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        dims = []
    return [str(d) for d in dims] if isinstance(dims, list) else []


def _all_group_tokens(window_id: int) -> list[str]:
    rows = db.fetchall(
        "SELECT DISTINCT dimension_key FROM aggregation_results WHERE window_id=? "
        "ORDER BY dimension_key",
        [window_id],
    )
    return [r["dimension_key"] for r in rows]


def _split_token(token: str) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for chunk in token.split("|"):
        if "=" in chunk:
            dim, value = chunk.split("=", 1)
            pairs.append((dim, value))
        else:
            pairs.append(("", chunk))
    return pairs


def version_summary(window_id: int, version: int) -> dict[str, Any]:
    """窗口某版本的完成情况。"""
    rows = db.fetchall(
        "SELECT * FROM aggregation_versions WHERE window_id=? AND computation_version=?",
        [window_id, version],
    )
    recalculating = [r["dimension_key"] for r in rows if r["status"] != "已完成"]
    if not rows:
        status = "未生成"
    elif not recalculating:
        status = "已完成"
    elif len(recalculating) == len(rows):
        status = "重算中"
    else:
        status = "部分完成"
    return {
        "window_id": window_id,
        "computation_version": version,
        "status": status,
        "recalculating": recalculating,
        "completed_groups": [r["dimension_key"] for r in rows if r["status"] == "已完成"],
    }


class RealtimeAggService(BaseModuleService):
    MODULE = "realtime_agg"
    ENTITY = "聚合窗口"
    OPERATION_TERM = "聚合"
    LIST_FIELDS = ["窗口名称", "数据源", "聚合维度", "聚合函数", "窗口大小"]
    REQUIRED_FIELDS = ["窗口名称", "数据源", "聚合维度"]
    STATUS_ORDER = ["草稿", "已启用", "已停用"]
    ACTION_RULES = {"启用聚合": "已启用", "停用聚合": "已停用"}
    NEGATIVE_ACTIONS = ["停用聚合"]
    EXTRA_DEFAULTS = {"window_type": "tumbling"}

    # ================================================================
    # 扩展查询
    # ================================================================

    def list_windows(
        self, status: str | None = None, data_source: str | None = None,
        page: int = 1, size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        """列出聚合窗口配置，支持按数据源筛选。"""
        where = "1=1"
        params: list[Any] = []
        if status:
            where += " AND status=?"
            params.append(status)
        if data_source:
            where += " AND data_source_module=?"
            params.append(data_source)

        total = db.count("aggregation_windows", where, params)
        rows = db.fetchall(
            f"SELECT * FROM aggregation_windows WHERE {where} ORDER BY id DESC LIMIT ? OFFSET ?",
            params + [size, (page - 1) * size],
        )
        return _sqlite_rows_to_dict(rows), total

    def results_by_window(
        self, window_id: int, limit: int = 50,
    ) -> list[dict[str, Any]]:
        """查询指定窗口的聚合结果。按窗口起始时间倒序。"""
        rows = db.fetchall(
            "SELECT * FROM aggregation_results WHERE window_id=? ORDER BY window_start DESC LIMIT ?",
            [window_id, limit],
        )
        return _sqlite_rows_to_dict(rows)

    def latest_value(
        self, window_id: int, dimension_key: str = "__all__",
    ) -> dict[str, Any] | None:
        """获取指定窗口和维度的最新聚合值。"""
        return db.fetchone(
            "SELECT * FROM aggregation_results WHERE window_id=? AND dimension_key=? "
            "ORDER BY window_start DESC LIMIT 1",
            [window_id, dimension_key],
        )

    # ================================================================
    # 聚合执行
    # ================================================================

    def run_aggregation(self, window_id: int, *, as_of: str | None = None) -> dict[str, Any]:
        """对指定窗口执行实时聚合计算。

        1. 从 aggregation_windows 加载窗口配置
        2. 调用 pipeline.compute_aggregation 执行计算（as_of 可固定窗口截止时刻）
        3. 返回本次计算的结果摘要
        """
        window = db.fetchone("SELECT * FROM aggregation_windows WHERE id=?", [window_id])
        if window is None:
            return {"ok": False, "message": f"窗口 {window_id} 不存在", "results": []}

        if window["status"] != "已启用":
            return {"ok": False, "message": f"窗口 {window['window_name']} 未启用，当前状态: {window['status']}", "results": []}

        results = compute_aggregation(dict(window), as_of=as_of)

        return {
            "ok": True,
            "message": f"窗口 {window['window_name']} 完成聚合，产出 {len(results)} 条结果",
            "results": results,
            "window_name": window["window_name"],
            "window_type": window["window_type"],
            "total_value": sum(r["computed_value"] for r in results),
        }

    def run_region_version(
        self,
        window_id: int,
        *,
        region_dimension: str,
        region_value: str,
        as_of: str | None = None,
        version: int | None = None,
    ) -> dict[str, Any]:
        """只对某个地区重算，并登记为一个版本。

        - version 为空：分配新版本，只有该地区的分组是「已完成」，
          其它分组在同一版本上登记为「重算中」——这正是「东北区出了新版、
          西南区还在重算」的中间态。
        - version 指定：把该地区写入已存在的版本（补齐），
          该地区分组转为「已完成」；该版本要等所有分组完成才算整版齐全。
        """
        window = db.fetchone("SELECT * FROM aggregation_windows WHERE id=?", [window_id])
        if window is None:
            return {"ok": False, "message": f"窗口 {window_id} 不存在", "results": []}
        if window["status"] != "已启用":
            return {"ok": False, "message": f"窗口 {window['window_name']} 未启用", "results": []}

        dims = _window_dims(window)
        if region_dimension not in dims:
            return {
                "ok": False,
                "message": f"窗口 {window['window_name']} 未按 {region_dimension} 分组，无法单独重算地区",
                "results": [],
            }

        target_version = int(version) if version is not None else next_computation_version(window_id)

        if version is None:
            # 其余分组先登记为「重算中」，保证窗口最新版本不等于两边齐全的版本
            for token in _all_group_tokens(window_id):
                if any(
                    dim == region_dimension and value == region_value
                    for dim, value in _split_token(token)
                ):
                    continue
                db.execute(
                    "INSERT INTO aggregation_versions"
                    "(window_id,computation_version,dimension_key,status,event_count,as_of,started_at) "
                    "VALUES(?,?,?,'重算中',0,?,?) "
                    "ON CONFLICT(window_id,computation_version,dimension_key) DO UPDATE SET "
                    "status='重算中'",
                    [window_id, target_version, token, as_of or db.now(), db.now()],
                )

        results = compute_aggregation(
            dict(window),
            as_of=as_of,
            version=target_version,
            scope={region_dimension: region_value},
        )

        summary = version_summary(window_id, target_version)
        if not results:
            return {
                "ok": False,
                "message": (
                    f"窗口 {window['window_name']} 地区 {region_value} 在当前基准时刻没有可聚合的事件"
                ),
                "results": [],
                "computation_version": target_version,
                "version_status": summary["status"],
            }
        return {
            "ok": True,
            "message": (
                f"窗口 {window['window_name']} 地区 {region_value} 写入版本 {target_version}，"
                f"产出 {len(results)} 条结果"
            ),
            "results": results,
            "window_name": window["window_name"],
            "region_dimension": region_dimension,
            "region_value": region_value,
            "computation_version": target_version,
            "version_status": summary["status"],
            "recalculating": summary["recalculating"],
        }

    def run_all_active(self) -> dict[str, Any]:
        """对所有已启用窗口执行聚合。"""
        windows = db.fetchall("SELECT * FROM aggregation_windows WHERE status='已启用'")
        summary = []
        total_results = 0
        for w in windows:
            r = self.run_aggregation(w["id"])
            summary.append({"window_name": w["window_name"], "result_count": len(r["results"])})
            total_results += len(r["results"])
        return {"ok": True, "windows_processed": len(windows), "total_results": total_results, "summary": summary}

    def time_range_analysis(
        self, window_id: int, start_time: str, end_time: str,
    ) -> dict[str, Any]:
        """对指定时间范围的聚合结果做趋势分析。"""
        rows = db.fetchall(
            "SELECT * FROM aggregation_results WHERE window_id=? AND window_start>=? AND window_start<=? "
            "ORDER BY window_start ASC",
            [window_id, start_time, end_time],
        )
        if not rows:
            return {"trend": "no_data", "points": []}

        values = [r["computed_value"] for r in rows]
        points = [{"time": r["window_start"], "value": r["computed_value"], "count": r["event_count"]} for r in rows]

        import statistics
        mean_v = statistics.mean(values)
        std_v = statistics.stdev(values) if len(values) > 1 else 0
        trend = "stable"
        if len(values) >= 2:
            if values[0] < values[-1] * 0.7:
                trend = "upward"
            elif values[0] > values[-1] * 1.3:
                trend = "downward"

        return {
            "trend": trend,
            "mean": round(mean_v, 4),
            "std": round(std_v, 4),
            "min": round(min(values), 4),
            "max": round(max(values), 4),
            "points": points,
        }

    def domain_snapshot(self) -> dict[str, Any]:
        snapshot = super().domain_snapshot()
        enabled = db.count("aggregation_windows", "status='已启用'")
        total_events = db.count("events")
        total_results = db.count("aggregation_results")
        snapshot["enabled_windows"] = enabled
        snapshot["total_ingested_events"] = total_events
        snapshot["total_agg_results"] = total_results
        snapshot["latest_batch"] = db.fetchone("SELECT batch_id, MAX(ingested_at) as ts FROM events")
        return snapshot


def _sqlite_rows_to_dict(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return rows