"""数据订阅领域服务：订阅指标变化并在满足条件时触发推送。

真实数据流：定义订阅任务（关联指标/推送条件/推送渠道）
→ 定时检查 metric_values 最新值 → 匹配推送条件 → 更新 subscriptions 推送时间。
"""
from __future__ import annotations

import json
from typing import Any

from app.database import db
from app.services.base import BaseModuleService


class DataSubscriptionService(BaseModuleService):
    MODULE = "data_subscription"
    ENTITY = "订阅任务"
    OPERATION_TERM = "订阅"
    LIST_FIELDS = ["订阅名称", "关联指标", "推送条件", "推送渠道", "生效状态"]
    REQUIRED_FIELDS = ["订阅名称", "关联指标", "推送条件"]
    STATUS_ORDER = ["待生效", "已生效", "已暂停"]
    ACTION_RULES = {"启用订阅": "已生效", "暂停订阅": "已暂停"}
    NEGATIVE_ACTIONS = ["暂停订阅"]

    def check_subscriptions(self) -> dict[str, Any]:
        """检查所有已生效订阅，满足条件的记录推送。"""
        subs = db.fetchall("SELECT * FROM subscriptions WHERE status='已生效'")
        results = []

        for sub in subs:
            metric_name = sub.get("关联指标", sub.get("metric_id", ""))
            push_condition_raw = sub.get("推送条件", sub.get("push_condition", "{}"))

            try:
                condition = json.loads(push_condition_raw) if isinstance(push_condition_raw, str) else push_condition_raw
            except (json.JSONDecodeError, TypeError):
                condition = {}

            metric = db.fetchone("SELECT id FROM metrics WHERE metric_name LIKE ?", [f"%{metric_name}%"])
            if not metric:
                results.append({"sub_name": sub["sub_name"], "triggered": False, "reason": "指标未找到"})
                continue

            latest = db.fetchone(
                "SELECT snapshot_value FROM metric_values WHERE metric_id=? ORDER BY snapshot_time DESC LIMIT 1",
                [metric["id"]],
            )
            if not latest:
                results.append({"sub_name": sub["sub_name"], "triggered": False, "reason": "无指标值"})
                continue

            triggered = _evaluate_push_condition(latest["snapshot_value"], condition)
            if triggered:
                db.update("subscriptions", {"last_pushed_at": db.now()}, "id=?", [sub["id"]])
                results.append({
                    "sub_name": sub["sub_name"],
                    "triggered": True,
                    "value": latest["snapshot_value"],
                    "channel": sub.get("推送渠道", sub.get("push_channel", "")),
                })
            else:
                results.append({"sub_name": sub["sub_name"], "triggered": False, "value": latest["snapshot_value"]})

        triggered_count = sum(1 for r in results if r.get("triggered"))
        return {"checked": len(subs), "triggered": triggered_count, "details": results}

    def domain_snapshot(self) -> dict[str, Any]:
        snapshot = super().domain_snapshot()
        snapshot["total_subscriptions"] = db.count("subscriptions")
        snapshot["active"] = db.count("subscriptions", "status='已生效'")
        return snapshot


def _evaluate_push_condition(value: float, condition: dict[str, Any]) -> bool:
    op = condition.get("op", "gt")
    threshold = float(condition.get("value", 0))
    if op == "gt":
        return value > threshold
    elif op == "lt":
        return value < threshold
    elif op == "gte":
        return value >= threshold
    elif op == "lte":
        return value <= threshold
    elif op == "change_pct":
        return abs(value) > threshold
    return False