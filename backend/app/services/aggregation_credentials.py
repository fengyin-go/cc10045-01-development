"""聚合凭据与版本化下钻：图表汇总数据的「可核对」视图。

解决的问题：

1. 图表只返回数值，分析员看不到这个数怎么算出来的。
   汇总凭据给出窗口、维度、计算版本、基准时刻、贡献事件数。
2. 下钻必须与汇总严格对得上：同一份凭据的窗口 + 版本 + 维度分组
   回查贡献事件，返回的明细条数须等于凭据里的贡献事件数。
3. 一次下钻只用一个「已完成」的版本，不会把两个版本的样本混在一起。
4. 关键维度缺失的值单独归到「（缺失）」组，与事件里真的填了 "?" 的取值分开。
5. 同一聚合窗口被两个图表（例如东北区 / 西南区）引用时，两边可能处在不同
   版本：一边已出新版，另一边还在重算。此时可以单独看任一地区的明细，
   也可以并排比较——但并排比较只取两边都齐全的同一版本。

版本模型：

- 计算版本按「窗口 × 版本 × 维度分组」登记在 aggregation_versions。
- 某地区出新版时只把该地区的分组标成「已完成」，其余分组登记为「重算中」，
  因此窗口的最新版本 ≠ 两个地区都齐全的版本。
- 凭据里的版本是这条汇总结果实际写入的版本，可随时按它回查。
"""
from __future__ import annotations

import json
from typing import Any

from app.database import db
from app.pipeline import MISSING_DIMENSION_VALUE, MISSING_DIMENSION_TOKEN


MISSING_GROUP_LABEL = MISSING_DIMENSION_VALUE


def _loads(raw: Any, fallback: Any) -> Any:
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw) if raw else fallback
    except (json.JSONDecodeError, TypeError):
        return fallback


def split_dimension_key(dimension_key: str) -> list[tuple[str, str]]:
    """把存储键拆成 [(维度名, 取值token)]；__all__ 视为空维度。"""
    if dimension_key in ("", "__all__"):
        return []
    pairs: list[tuple[str, str]] = []
    for chunk in dimension_key.split("|"):
        if "=" in chunk:
            dim, value = chunk.split("=", 1)
            pairs.append((dim, value))
        else:
            pairs.append(("", chunk))
    return pairs


def decode_dimension_key(dimension_key: str, dims: list[str] | None = None) -> dict[str, Any]:
    """把存储用的分组键还原成维度取值 + 缺失信息。"""
    pairs = split_dimension_key(dimension_key)
    if not pairs:
        return {
            "dimension_values": {},
            "missing_dimensions": [],
            "label": "全部",
            "is_missing_group": False,
        }
    values: dict[str, Any] = {}
    missing: list[str] = []
    labels: list[str] = []
    for index, (dim, value) in enumerate(pairs):
        name = dim or (dims[index] if dims and index < len(dims) else f"dim{index}")
        if value == MISSING_DIMENSION_TOKEN:
            values[name] = None
            missing.append(name)
            labels.append(MISSING_GROUP_LABEL)
        else:
            values[name] = value
            labels.append(value)
    return {
        "dimension_values": values,
        "missing_dimensions": missing,
        "label": "|".join(labels),
        "is_missing_group": bool(missing),
    }


def group_matches(dimension_key: str, dim: str, value: str) -> bool:
    return any(d == dim and v == value for d, v in split_dimension_key(dimension_key))


def _chart_window(entry: dict[str, Any]) -> dict[str, Any] | None:
    """把图表配置里的窗口引用解析成聚合窗口行。"""
    window = entry.get("window") or {}
    window_id = window.get("window_id", entry.get("window_id"))
    if window_id is not None:
        row = db.fetchone("SELECT * FROM aggregation_windows WHERE id=?", [window_id])
        if row:
            return row
    name = window.get("name") or entry.get("window_name")
    if name:
        return db.fetchone("SELECT * FROM aggregation_windows WHERE window_name=?", [name])
    return None


def _region_of(entry: dict[str, Any], chart: dict[str, Any]) -> tuple[str | None, str | None]:
    window = chart.get("window") or {}
    dim = (
        window.get("region_dimension") or chart.get("region_dimension")
        or entry.get("region_dimension") or "region"
    )
    value = window.get("region_value")
    if value is None:
        value = chart.get("region_value")
    if value is None:
        value = entry.get("region_value")
    return dim, (None if value is None else str(value))


def _version_rows(window_id: int) -> list[dict[str, Any]]:
    rows = db.fetchall(
        "SELECT * FROM aggregation_versions WHERE window_id=? "
        "ORDER BY computation_version, dimension_key",
        [window_id],
    )
    if rows:
        return rows
    # 老库没有版本登记表数据时，从结果表回推版本（各分组均视为已完成）。
    return db.fetchall(
        "SELECT r.window_id, r.computation_version, r.dimension_key, '已完成' AS status, "
        "r.id AS result_id, r.event_count, r.window_start, r.window_end, "
        "r.created_at AS as_of, r.created_at AS started_at, r.created_at AS completed_at "
        "FROM aggregation_results r WHERE r.window_id=? "
        "ORDER BY r.computation_version, r.dimension_key",
        [window_id],
    )


def version_regions(window_id: int) -> dict[int, dict[str, Any]]:
    """按版本汇总分组完成情况。

    {version: {"groups": [...], "regions": {region_value: status}, "status": ...}}
    注意「重算中」的分组也会登记，因此不能据某版本存在就认为它齐全。
    """
    summary: dict[int, dict[str, Any]] = {}
    for row in _version_rows(window_id):
        version = int(row["computation_version"])
        bucket = summary.setdefault(
            version,
            {"version": version, "groups": [], "regions": {}, "complete": True,
             "recalculating": [], "as_of": row.get("as_of")},
        )
        bucket["groups"].append(row)
        # 版本是否齐全要按「全部分组」判断；地区键只用于地区视图。
        if row["status"] == "已完成":
            continue
        bucket["complete"] = False
        bucket["recalculating"].append(row["dimension_key"])
    for bucket in summary.values():
        bucket["regions"] = {
            row["dimension_key"]: row["status"] for row in bucket["groups"]
        }
        if bucket["complete"]:
            bucket["status"] = "已完成"
        elif bucket["recalculating"] and len(bucket["recalculating"]) == len(bucket["groups"]):
            bucket["status"] = "重算中"
        else:
            bucket["status"] = "部分完成"
    return summary


def _group_status(
    versions: dict[int, dict[str, Any]], version: int, *,
    region_dim: str | None, region_value: str | None,
    dimension_key: str | None = None,
) -> str | None:
    """某分组/地区在某版本上的状态。

    - 指定了 dimension_key：直接查该分组；
    - 只指定地区：该地区在版本内任一已完成分组即视为可用；
    - 都没指定：整版完整才可用。
    """
    bucket = versions.get(version)
    if bucket is None:
        return None
    if dimension_key:
        for row in bucket["groups"]:
            if row["dimension_key"] == dimension_key:
                return row["status"]
        return None
    if region_dim and region_value is not None:
        statuses = [
            row["status"] for row in bucket["groups"]
            if group_matches(row["dimension_key"], region_dim, region_value)
        ]
        if not statuses:
            return None
        return "已完成" if any(s == "已完成" for s in statuses) else statuses[0]
    return "已完成" if bucket["complete"] else bucket.get("status")


def _pick_version(
    versions: dict[int, dict[str, Any]],
    *,
    region_dim: str | None,
    region_value: str | None,
    requested: int | None,
    dimension_key: str | None = None,
) -> tuple[int | None, str | None]:
    """选版本。

    未指定版本：取该地区（或该分组）已完成的最高版本；
    指定版本：该版本在这条分组上必须是「已完成」，否则不返回数据。
    返回 (version, error_message)。
    """
    if requested is not None:
        bucket = versions.get(int(requested))
        if bucket is None:
            return int(requested), f"版本 {requested} 尚未生成"
        status = _group_status(
            versions, int(requested), region_dim=region_dim,
            region_value=region_value, dimension_key=dimension_key,
        )
        if status != "已完成":
            return int(requested), (
                f"版本 {requested} 在该分组上的状态为{status or '未登记'}，不能作为取数依据"
            )
        return int(requested), None

    candidates = [
        version for version in sorted(versions, reverse=True)
        if _group_status(
            versions, version, region_dim=region_dim,
            region_value=region_value, dimension_key=dimension_key,
        ) == "已完成"
    ]
    if not candidates:
        return None, "没有已完成的计算版本"
    return candidates[0], None


def summarize_chart(
    chart: dict[str, Any],
    *,
    region_group: str | None = None,
    version: int | None = None,
    all_groups: bool = False,
) -> dict[str, Any]:
    """为单个图表生成汇总凭据。

    展示窗口、维度、计算版本、基准时刻、贡献事件数；并标出缺失组，
    方便分析员把汇总条数与下钻明细条数对上。

    all_groups=True 时忽略图表上的地区过滤，返回该窗口该版本的全部分组，
    用于查看「（缺失）」组与取值为 "?" 的分组。
    """
    window = _chart_window(chart)
    if window is None:
        return {
            "ok": False,
            "title": chart.get("title", "图表"),
            "message": "图表未绑定有效聚合窗口",
        }

    dims = _loads(window.get("dimensions"), [])
    if not isinstance(dims, list):
        dims = []
    dims = [str(d) for d in dims]

    region_dim, region_value = _region_of(chart, chart)
    versions = version_regions(window["id"])
    if all_groups:
        # 全分组视图不绑定单一地区，只锁定版本齐全性
        region_dim, region_value = None, None
    used_version, error = _pick_version(
        versions, region_dim=region_dim, region_value=region_value, requested=version,
    )

    latest_window_version = max(versions) if versions else None
    pending_version = None
    if used_version is not None and latest_window_version is not None and latest_window_version > used_version:
        newest = versions[latest_window_version]
        if not newest["complete"]:
            pending_version = {
                "version": latest_window_version,
                "status": newest["status"],
                "recalculating": newest["recalculating"],
            }

    base = {
        "ok": error is None,
        "title": chart.get("title", window["window_name"]),
        "dashboard_id": chart.get("dashboard_id"),
        "window": {
            "window_id": window["id"],
            "window_name": window["window_name"],
            "window_type": window["window_type"],
            "window_size_seconds": window["window_size_seconds"],
            "agg_function": window["agg_function"],
        },
        "dimensions": dims,
        "region_dimension": region_dim,
        "region_value": region_value,
        "region_group": region_group,
        "computation_version": used_version,
        "latest_window_version": latest_window_version,
        "pending_version": pending_version,
        "groups": [],
    }
    if error is not None:
        base["message"] = error
        base["version_status"] = (
            versions.get(int(used_version))["status"] if used_version in versions else "未生成"
        )
        base["contribution_count"] = 0
        return base

    rows = db.fetchall(
        "SELECT * FROM aggregation_results WHERE window_id=? AND computation_version=? "
        "ORDER BY dimension_key",
        [window["id"], used_version],
    )
    if region_dim and region_value is not None:
        rows = [r for r in rows if group_matches(r["dimension_key"], region_dim, region_value)]

    groups: list[dict[str, Any]] = []
    total_contribution = 0
    total_value = 0.0
    for row in rows:
        info = decode_dimension_key(row["dimension_key"], dims)
        contribution = db.count("aggregation_result_events", "result_id=?", [row["id"]])
        groups.append({
            "result_id": row["id"],
            "dimension_key": row["dimension_key"],
            "dimension_label": info["label"],
            "dimension_values": info["dimension_values"],
            "missing_dimensions": info["missing_dimensions"],
            "is_missing_group": info["is_missing_group"],
            "computed_value": row["computed_value"],
            "event_count": row["event_count"],
            "contribution_count": contribution,
            "computation_version": row["computation_version"],
            "window_start": row["window_start"],
            "window_end": row["window_end"],
        })
        total_contribution += contribution
        total_value += row["computed_value"]

    bucket = versions.get(int(used_version), {"status": "未生成", "as_of": None})
    base.update({
        "version_status": bucket["status"],
        "as_of": bucket.get("as_of"),
        "window_start": rows[0]["window_start"] if rows else None,
        "window_end": rows[0]["window_end"] if rows else None,
        "contribution_count": total_contribution,
        "value_total": round(total_value, 4),
        "groups": groups,
        "missing_group": next((g for g in groups if g["is_missing_group"]), None),
        "question_mark_groups": [
            g for g in groups
            if any(v == "?" for v in g["dimension_values"].values() if v is not None)
        ],
    })
    return base


def drill_down(
    *,
    window_id: int,
    dimension_key: str,
    computation_version: int,
    limit: int = 500,
) -> dict[str, Any]:
    """按凭据回查贡献明细。

    只读指定窗口 + 版本 + 分组的那一条结果，返回它登记的贡献事件；
    returned_count 与凭据里的 contribution_count 必须一致。
    """
    result = db.fetchone(
        "SELECT * FROM aggregation_results WHERE window_id=? AND computation_version=? "
        "AND dimension_key=?",
        [window_id, computation_version, dimension_key],
    )
    if result is None:
        return {
            "ok": False,
            "message": f"窗口 {window_id} 版本 {computation_version} 中不存在分组 {dimension_key}",
            "events": [],
            "returned_count": 0,
        }

    window = db.fetchone("SELECT * FROM aggregation_windows WHERE id=?", [window_id])
    dims = _loads(window.get("dimensions"), []) if window else []
    info = decode_dimension_key(dimension_key, dims)

    total = db.count("aggregation_result_events", "result_id=?", [result["id"]])
    events = db.fetchall(
        "SELECT e.* FROM events e JOIN aggregation_result_events map ON map.event_id=e.id "
        "WHERE map.result_id=? ORDER BY e.occurred_at DESC, e.id DESC LIMIT ?",
        [result["id"], limit],
    )

    return {
        "ok": True,
        "window_id": window_id,
        "dimension_key": dimension_key,
        "dimension_label": info["label"],
        "dimension_values": info["dimension_values"],
        "missing_dimensions": info["missing_dimensions"],
        "is_missing_group": info["is_missing_group"],
        "computation_version": computation_version,
        "window_start": result["window_start"],
        "window_end": result["window_end"],
        "computed_value": result["computed_value"],
        "summary_event_count": result["event_count"],
        "contribution_count": total,
        "returned_count": len(events),
        "matched": len(events) == total,
        "truncated": total > len(events),
        "events": [
            {
                "id": e["id"],
                "event_name": e["event_name"],
                "occurred_at": e["occurred_at"],
                "event_attrs": _loads(e.get("event_attrs"), {}),
            }
            for e in events
        ],
    }


def compare_regions(
    *,
    window_id: int,
    dimension_key: str,
    left: dict[str, Any],
    right: dict[str, Any],
    region_dimension: str = "region",
) -> dict[str, Any]:
    """并排比较两个地区。

    锁定两边都齐全的同一版本：
    - 取两边都登记为「已完成」的版本的交集，选最高版本；
    - 交集为空时返回 ok=False，并给出两边各自最新版本；
    - 两边最新版本一致且都齐全时标记 version_aligned=True。
    """
    versions = version_regions(window_id)
    left_versions = {
        v for v in versions
        if _group_status(
            versions, v, region_dim=region_dimension,
            region_value=left["region_value"],
        ) == "已完成"
    }
    right_versions = {
        v for v in versions
        if _group_status(
            versions, v, region_dim=region_dimension,
            region_value=right["region_value"],
        ) == "已完成"
    }
    shared = sorted(left_versions & right_versions, reverse=True)
    if not shared:
        return {
            "ok": False,
            "message": "两个地区没有共同齐全的计算版本，暂不能并排比较",
            "window_id": window_id,
            "dimension_key": dimension_key,
            "left_latest_version": max(left_versions) if left_versions else None,
            "right_latest_version": max(right_versions) if right_versions else None,
        }

    common_version = shared[0]
    panels = []
    for side, region in (("left", left), ("right", right)):
        summary = summarize_chart(
            {
                "title": region.get("label", side),
                "window": {
                    "window_id": window_id,
                    "region_dimension": region_dimension,
                    "region_value": region["region_value"],
                },
            },
            region_group=side,
            version=common_version,
        )
        panels.append({
            "side": side,
            "label": region.get("label", side),
            "region_value": region["region_value"],
            "latest_version": max(
                v for v in versions
                if _group_status(
                    versions, v, region_dim=region_dimension,
                    region_value=region["region_value"], dimension_key=dimension_key or None,
                ) == "已完成"
            ),
            "in_common_version": True,
            "version_status": summary.get("version_status"),
            "summary": summary,
        })

    return {
        "ok": True,
        "window_id": window_id,
        "dimension_key": dimension_key,
        "region_dimension": region_dimension,
        "computation_version": common_version,
        "left_latest_version": max(left_versions),
        "right_latest_version": max(right_versions),
        "version_aligned": max(left_versions) == max(right_versions) == common_version,
        "panels": panels,
    }
