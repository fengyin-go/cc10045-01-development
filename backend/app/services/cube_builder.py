"""多维数据集构建领域服务：定义多维Cube并执行预计算。

真实数据流：定义Cube（事实表/维度组合/指标计算）
→ pipeline.precompute_cube 从 events 表按维度组合聚合
→ 写入 cube_cells → 下游仪表盘查询加速。
"""
from __future__ import annotations

import json
from typing import Any

from app.database import db
from app.pipeline import precompute_cube
from app.services.base import BaseModuleService


class CubeBuilderService(BaseModuleService):
    MODULE = "cube_builder"
    ENTITY = "Cube定义"
    OPERATION_TERM = "预计算"
    LIST_FIELDS = ["Cube名称", "事实表", "维度组合", "指标计算", "预计算状态"]
    REQUIRED_FIELDS = ["Cube名称", "事实表", "维度组合", "指标计算"]
    STATUS_ORDER = ["草稿", "已启用", "已停用"]
    ACTION_RULES = {"启用Cube": "已启用", "停用Cube": "已停用"}
    NEGATIVE_ACTIONS = []

    def run_precomputation(self, cube_id: int) -> dict[str, Any]:
        """执行Cube预计算。"""
        cube = db.fetchone("SELECT * FROM cubes WHERE id=?", [cube_id])
        if cube is None:
            return {"ok": False, "message": f"Cube {cube_id} 不存在"}

        db.update("cubes", {"precomputation_status": "计算中"}, "id=?", [cube_id])
        cell_count = precompute_cube(dict(cube))

        return {
            "ok": True,
            "cube_name": cube["cube_name"],
            "cells_computed": cell_count,
            "status": "已启用",
        }

    def cube_cells(self, cube_id: int, limit: int = 100) -> list[dict[str, Any]]:
        """获取Cube预计算结果。"""
        rows = db.fetchall(
            "SELECT * FROM cube_cells WHERE cube_id=? ORDER BY measure_value DESC LIMIT ?",
            [cube_id, limit],
        )
        for r in rows:
            try:
                r["dimension_values"] = json.loads(r["dimension_values"]) if isinstance(
                    r["dimension_values"], str) else r["dimension_values"]
            except json.JSONDecodeError:
                pass
        return rows

    def aggregate_by_dimension(self, cube_id: int, dimension: str) -> list[dict[str, Any]]:
        """按指定维度汇总Cube数据。"""
        rows = db.fetchall(
            "SELECT dimension_values, SUM(measure_value) as total FROM cube_cells WHERE cube_id=? GROUP BY 1",
            [cube_id],
        )
        result: list[dict[str, Any]] = []
        for r in rows:
            try:
                dims = json.loads(r["dimension_values"]) if isinstance(r["dimension_values"], str) else {}
                val = dims.get(dimension, "unknown")
            except (json.JSONDecodeError, TypeError):
                val = "parse_error"
            result.append({"dimension_value": val, "total": r["total"]})
        return sorted(result, key=lambda x: -x["total"])

    def domain_snapshot(self) -> dict[str, Any]:
        snapshot = super().domain_snapshot()
        snapshot["total_cubes"] = db.count("cubes")
        snapshot["total_cells"] = db.count("cube_cells")
        snapshot["enabled_cubes"] = db.count("cubes", "precomputation_status='已启用'")
        return snapshot