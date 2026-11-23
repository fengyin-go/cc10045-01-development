"""统一资源接口：查询、导出、登记和动作按模块 key 分派到领域服务。"""
from __future__ import annotations

import csv
import io
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.schemas import ActionPayload, ActionResult, EntryPayload, PageResult
from app.services import service_for

router = APIRouter(prefix="/api", tags=["业务资源"])


def require_service(module: str):
    service = service_for(module)
    if service is None:
        raise HTTPException(status_code=404, detail=f"未知业务模块：{module}")
    return service


@router.get("/{module}", response_model=PageResult[dict])
def list_entries(
    module: str,
    request: Request,
    keyword: str | None = Query(default=None),
    status: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=200),
) -> PageResult[dict]:
    service = require_service(module)
    filters = {
        key: value.strip()
        for key, value in request.query_params.items()
        if key in service.LIST_FIELDS and value.strip()
    }
    items, total = service.list_entries(
        filters=filters, keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/{module}/export")
def export_entries(
    module: str,
    request: Request,
    keyword: str | None = Query(default=None),
    status: str | None = Query(default=None),
) -> StreamingResponse:
    service = require_service(module)
    filters = {
        key: value.strip()
        for key, value in request.query_params.items()
        if key in service.LIST_FIELDS and value.strip()
    }
    items, _ = service.list_entries(
        filters=filters, keyword=keyword, status=status, page=1, size=1_000_000)
    output = io.StringIO()
    output.write("\ufeff")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["id", *service.LIST_FIELDS, "status"])
    for item in items:
        values = [item.get("id"), *(item.get(field, "") for field in service.LIST_FIELDS),
                  item.get("status", "")]
        writer.writerow([
            "'" + str(value) if str(value or "").startswith(("=", "+", "-", "@")) else value
            for value in values
        ])
    return StreamingResponse(
        iter([output.getvalue()]), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{module}.csv"'},
    )


@router.get("/{module}/{entry_id}", response_model=dict)
def get_entry(module: str, entry_id: int) -> dict[str, Any]:
    service = require_service(module)
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"{service.ENTITY} {entry_id} 不存在或已归档")
    return entry


# ================================================================
# 汇总凭据：图表数值的窗口 / 维度 / 计算版本 / 贡献事件数
# ================================================================

@router.get("/dashboard_builder/{dashboard_id}/credentials", response_model=dict)
def dashboard_credentials(
    dashboard_id: int,
    version: int | None = Query(default=None, ge=1),
    all_groups: bool = Query(default=False),
) -> dict[str, Any]:
    """仪表盘内全部图表的汇总凭据；带 version 时按该版本回查。"""
    service = require_service("dashboard_builder")
    return service.chart_credentials(dashboard_id, version=version, all_groups=all_groups)


@router.get("/dashboard_builder/{dashboard_id}/charts/{chart_index}/credential", response_model=dict)
def chart_credential(
    dashboard_id: int,
    chart_index: int,
    version: int | None = Query(default=None, ge=1),
    all_groups: bool = Query(default=False),
) -> dict[str, Any]:
    """单张图表的汇总凭据。"""
    service = require_service("dashboard_builder")
    return service.chart_credential(
        dashboard_id, chart_index, version=version, all_groups=all_groups)


@router.get("/dashboard_builder/{dashboard_id}/charts/{chart_index}/compare", response_model=dict)
def compare_chart_regions(
    dashboard_id: int,
    chart_index: int,
    left: str = Query(min_length=1),
    right: str = Query(min_length=1),
    region_dimension: str = Query(default="region"),
    dimension_key: str = Query(default=""),
) -> dict[str, Any]:
    """并排比较同窗口下两个地区，锁定两边都齐全的同一版本。"""
    service = require_service("dashboard_builder")
    return service.compare_chart_regions(
        dashboard_id, chart_index,
        left_region=left, right_region=right,
        region_dimension=region_dimension, dimension_key=dimension_key,
    )


@router.post("/drill_path/drill", response_model=dict)
def drill_from_credential(payload: dict[str, Any]) -> dict[str, Any]:
    """按汇总凭据的窗口 + 版本 + 维度分组下钻，返回贡献明细。"""
    missing = [
        key for key in ("window_id", "dimension_key", "computation_version")
        if payload.get(key) in (None, "")
    ]
    if missing:
        raise HTTPException(status_code=422, detail=f"缺少参数：{'、'.join(missing)}")
    service = require_service("drill_path")
    return service.drill_from_credential(
        window_id=int(payload["window_id"]),
        dimension_key=str(payload["dimension_key"]),
        computation_version=int(payload["computation_version"]),
        limit=int(payload.get("limit", 500)),
    )


@router.post("/{module}", response_model=ActionResult)
def create_entry(module: str, payload: EntryPayload) -> ActionResult:
    service = require_service(module)
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message=f"{service.ENTITY}已登记", entry=entry)


@router.post("/{module}/{entry_id}/actions", response_model=ActionResult)
def run_action(module: str, entry_id: int, payload: ActionPayload) -> ActionResult:
    service = require_service(module)
    entry, message = service.run_action(entry_id, payload.action.strip())
    return ActionResult(ok=entry is not None, message=message, entry=entry)
