"""异常归因领域服务：对指标异常波动自动执行多维度归因和贡献度分析。

真实数据流：确定分析对象和异常时间点 → pipeline.detect_anomaly 执行 z-score 检测
→ 按归因维度解构贡献度 → 输出分析结论 → 写入 anomaly_analyses 表。
"""
from __future__ import annotations

import json
from typing import Any

from app.database import db
from app.pipeline import detect_anomaly
from app.services.base import BaseModuleService


class AnomalyAnalysisService(BaseModuleService):
    MODULE = "anomaly_analysis"
    ENTITY = "归因分析器"
    OPERATION_TERM = "分析"
    LIST_FIELDS = ["分析对象", "异常时间点", "归因维度", "贡献度排序", "分析结论"]
    REQUIRED_FIELDS = ["分析对象", "异常时间点", "归因维度"]
    STATUS_ORDER = ["待分析", "分析中", "已完成", "分析失败"]
    ACTION_RULES = {"启动分析": "已完成", "取消分析": "分析失败"}
    NEGATIVE_ACTIONS = []

    def run_analysis(self, entry_id: int) -> dict[str, Any]:
        """执行异常归因分析。先加载记录，再调用管线。"""
        entry = self.get_entry(entry_id)
        if entry is None:
            return {"ok": False, "message": f"分析记录 {entry_id} 不存在"}

        # 标记为分析中
        db.update("anomaly_analyses", {"status": "分析中"}, "id=?", [entry_id])

        analysis_data = {
            "分析对象": entry.get("分析对象"),
            "异常时间点": entry.get("异常时间点"),
            "归因维度": entry.get("归因维度"),
        }
        result = detect_anomaly(analysis_data)

        # 回写结论
        conclusion = result.get("conclusion", "分析完成")
        contributions = result.get("contributions", {})
        ranking_json = json.dumps(
            sorted(contributions.items(), key=lambda x: -x[1])[:5],
            ensure_ascii=False,
        )

        db.update("anomaly_analyses", {
            "status": "已完成",
            "contribution_ranking": ranking_json,
            "conclusion": conclusion,
        }, "id=?", [entry_id])

        return {
            "ok": True,
            "target": analysis_data["分析对象"],
            "is_anomaly": result.get("is_anomaly", False),
            "z_score": result.get("z_score", 0),
            "conclusion": conclusion,
            "contributions": contributions,
        }

    def recent_anomalies(self, limit: int = 20) -> list[dict[str, Any]]:
        """获取最近的分析结果。"""
        return db.fetchall(
            "SELECT * FROM anomaly_analyses WHERE status='已完成' ORDER BY id DESC LIMIT ?",
            [limit],
        )

    def domain_snapshot(self) -> dict[str, Any]:
        snapshot = super().domain_snapshot()
        snapshot["total_analyses"] = db.count("anomaly_analyses")
        snapshot["completed_analyses"] = db.count("anomaly_analyses", "status='已完成'")
        return snapshot