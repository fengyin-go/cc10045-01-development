"""报告快照领域服务：保存报告的完整数据快照并支持历史版本对比。

真实数据流：从 report_outputs 读取已有报告内容 → 生成快照记录写入 snapshots 表
→ 支持两版本对比 → 标记差异快照 → 供趋势分析和版本回溯。
"""
from __future__ import annotations

import json
from typing import Any

from app.database import db
from app.services.base import BaseModuleService


class ReportSnapshotService(BaseModuleService):
    MODULE = "report_snapshot"
    ENTITY = "快照记录"
    OPERATION_TERM = "存储"
    LIST_FIELDS = ["快照名称", "所属报告", "快照时间", "数据摘要", "差异对比"]
    REQUIRED_FIELDS = ["快照名称", "所属报告"]
    STATUS_ORDER = ["已保存", "已对比", "有差异"]
    ACTION_RULES = {"生成快照": "已保存", "对比快照": "已对比"}
    NEGATIVE_ACTIONS = []

    def create_for_report(self, report_id: int, snapshot_name: str) -> dict[str, Any]:
        """为指定报告的最新输出创建快照。"""
        latest_output = db.fetchone(
            "SELECT * FROM report_outputs WHERE report_id=? ORDER BY generated_at DESC LIMIT 1",
            [report_id],
        )
        if not latest_output:
            return {"ok": False, "message": f"报告 {report_id} 没有已生成的输出"}

        snapshot_id = db.insert("snapshots", {
            "snapshot_name": snapshot_name,
            "report_id": report_id,
            "snapshot_content": latest_output["output_content"],
            "status": "已保存",
        })
        return {
            "ok": True,
            "snapshot_id": snapshot_id,
            "snapshot_name": snapshot_name,
            "report_id": report_id,
        }

    def compare_snapshots(self, snapshot_id_a: int, snapshot_id_b: int) -> dict[str, Any]:
        """对比两个快照的差异。"""
        a = db.fetchone("SELECT * FROM snapshots WHERE id=?", [snapshot_id_a])
        b = db.fetchone("SELECT * FROM snapshots WHERE id=?", [snapshot_id_b])

        if not a or not b:
            return {"ok": False, "message": "快照不存在"}

        try:
            content_a = json.loads(a["snapshot_content"]) if isinstance(a["snapshot_content"], str) else a["snapshot_content"]
            content_b = json.loads(b["snapshot_content"]) if isinstance(b["snapshot_content"], str) else b["snapshot_content"]
        except json.JSONDecodeError:
            return {"ok": False, "message": "快照内容解析失败"}

        diffs = []
        sections_a = content_a.get("sections", [])
        sections_b = content_b.get("sections", [])

        for sa, sb in zip(sections_a, sections_b):
            val_a = sa.get("current_value", 0)
            val_b = sb.get("current_value", 0)
            if val_a and val_b:
                try:
                    diff_pct = round(float(val_a - val_b) / max(abs(float(val_b)), 0.0001) * 100, 2)
                    diffs.append({
                        "metric": sa.get("metric_name"),
                        "value_a": val_a,
                        "value_b": val_b,
                        "diff_pct": diff_pct,
                        "has_diff": abs(diff_pct) > 1.0,
                    })
                except (TypeError, ValueError):
                    pass

        has_diff = any(d["has_diff"] for d in diffs)
        comparison_summary = {
            "snapshot_a": snapshot_id_a,
            "snapshot_b": snapshot_id_b,
            "total_metrics": len(diffs),
            "diffs_found": sum(1 for d in diffs if d["has_diff"]),
            "details": diffs,
        }

        db.update("snapshots", {
            "status": "已对比" if not has_diff else "有差异",
            "comparison_diff": json.dumps(comparison_summary, ensure_ascii=False),
        }, "id=?", [snapshot_id_a])

        return {"ok": True, "has_difference": has_diff, "summary": comparison_summary}

    def report_snapshots(self, report_id: int) -> list[dict[str, Any]]:
        """获取报告的所有快照。"""
        return db.fetchall(
            "SELECT * FROM snapshots WHERE report_id=? ORDER BY created_at DESC",
            [report_id],
        )

    def domain_snapshot(self) -> dict[str, Any]:
        snapshot = super().domain_snapshot()
        snapshot["total_snapshots"] = db.count("snapshots")
        return snapshot