"""分析告警策略领域服务：定义指标的异常波动和阈值告警策略。

真实数据流：定义告警规则（监控指标/告警条件/连续触发次数/通知方式）
→ pipeline.check_alert 从 metric_values 读取最新值 → 对比告警条件
→ 达到连续阈值时触发告警日志 → 记录到 alert_log → 下游推送执行。
"""
from __future__ import annotations

from typing import Any

from app.database import db
from app.pipeline import check_alert
from app.services.base import BaseModuleService


class AlertPolicyService(BaseModuleService):
    MODULE = "alert_policy"
    ENTITY = "告警规则"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["规则名称", "监控指标", "告警条件", "通知方式", "连续触发次数"]
    REQUIRED_FIELDS = ["规则名称", "监控指标", "告警条件"]
    STATUS_ORDER = ["待生效", "已生效", "已关闭"]
    ACTION_RULES = {"启用告警": "已生效", "关闭告警": "已关闭"}
    NEGATIVE_ACTIONS = ["关闭告警"]

    def check_all(self) -> dict[str, Any]:
        """检查所有已生效告警规则。"""
        alerts = db.fetchall("SELECT * FROM alerts WHERE status='已生效'")
        results = []
        triggered_count = 0

        for alert in alerts:
            result = check_alert(dict(alert))
            if result:
                results.append({
                    "rule_name": alert["rule_name"],
                    "triggered": result.get("triggered", False),
                    "message": result.get("message", ""),
                })
                if result.get("triggered"):
                    triggered_count += 1

        return {
            "checked": len(alerts),
            "triggered": triggered_count,
            "details": results,
        }

    def check_single(self, alert_id: int) -> dict[str, Any]:
        """检查单个告警规则。"""
        alert = db.fetchone("SELECT * FROM alerts WHERE id=?", [alert_id])
        if alert is None:
            return {"ok": False, "message": f"告警规则 {alert_id} 不存在"}

        result = check_alert(dict(alert))
        return {
            "ok": True,
            "rule_name": alert["rule_name"],
            "check_result": result,
        }

    def alert_history(self, alert_id: int, limit: int = 50) -> list[dict[str, Any]]:
        """获取告警触发历史。"""
        return db.fetchall(
            "SELECT * FROM alert_log WHERE alert_id=? ORDER BY triggered_at DESC LIMIT ?",
            [alert_id, limit],
        )

    def resolve_alert(self, log_id: int) -> dict[str, Any]:
        """标记告警为已解决。"""
        db.update("alert_log", {"resolved": 1}, "id=?", [log_id])
        return {"ok": True, "message": "告警已标记为已解决"}

    def domain_snapshot(self) -> dict[str, Any]:
        snapshot = super().domain_snapshot()
        snapshot["total_rules"] = db.count("alerts")
        snapshot["active_rules"] = db.count("alerts", "status='已生效'")
        snapshot["today_triggers"] = db.count("alert_log")
        return snapshot