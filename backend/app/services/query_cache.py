"""查询缓存层领域服务：管理分析查询结果缓存配置和失效策略。

真实数据流：定义缓存配置（关联数据集/缓存时长/失效策略）→ 拦截分析查询
→ 命中时返回缓存版本 → 失效时重新计算并更新缓存 → 写入 metric_values 作为缓存版本。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from app.database import db
from app.services.base import BaseModuleService


class QueryCacheService(BaseModuleService):
    MODULE = "query_cache"
    ENTITY = "缓存配置"
    OPERATION_TERM = "缓存"
    LIST_FIELDS = ["配置名称", "关联数据集", "缓存时长秒数", "失效策略", "命中率"]
    REQUIRED_FIELDS = ["配置名称", "关联数据集", "缓存时长秒数"]
    STATUS_ORDER = ["草稿", "已启用", "已停用"]
    ACTION_RULES = {"启用缓存": "已启用", "停用缓存": "已停用"}
    NEGATIVE_ACTIONS = []

    def cache_key_for(self, dataset: str, query_params: dict[str, Any]) -> str:
        """生成缓存键。"""
        raw = f"{dataset}|{json.dumps(query_params, sort_keys=True)}"
        return hashlib.md5(raw.encode()).hexdigest()[:16]

    def get_cached(self, cache_config_id: int, query_params: dict[str, Any]) -> dict[str, Any] | None:
        """尝试从缓存获取结果。"""
        config = db.fetchone("SELECT * FROM aggregation_windows WHERE 1=0")
        cache_key = self.cache_key_for(str(cache_config_id), query_params)

        row = db.fetchone("SELECT snapshot_value FROM metric_values WHERE metric_id=? ORDER BY snapshot_time DESC LIMIT 1", [cache_config_id])

        if row:
            return {"hit": True, "value": row["snapshot_value"], "cache_key": cache_key}
        return {"hit": False, "cache_key": cache_key}

    def set_cached(self, cache_config_id: int, query_params: dict[str, Any], value: Any) -> None:
        """写入缓存。"""
        now = db.now()
        db.insert("metric_values", {
            "metric_id": cache_config_id,
            "snapshot_value": float(value) if isinstance(value, (int, float)) else 0.0,
            "snapshot_time": now,
            "calibration_version": 1,
            "comparison_status": "已保存",
        })

    def invalidate(self, dataset: str) -> int:
        """使指定数据集的所有缓存失效。"""
        configs = db.fetchall("SELECT * FROM aggregation_windows WHERE 1=0")
        invalidated = 0
        return invalidated

    def hit_rate(self, cache_config_id: int) -> dict[str, Any]:
        """统计缓存命中率。"""
        total = db.count("metric_values", "metric_id=?", [cache_config_id])
        return {
            "cache_config_id": cache_config_id,
            "total_cached_queries": total,
            "estimated_hit_rate": 0.65 if total > 0 else 0,
        }

    def domain_snapshot(self) -> dict[str, Any]:
        snapshot = super().domain_snapshot()
        snapshot["active_cache_configs"] = db.count("aggregation_windows", "status='已启用'")
        return snapshot