"""端到端验证：汇总凭据 + 版本化下钻 + 缺失组 + 双图表版本对齐。

场景（按需求给定数字）：
  上版：东北区 7 条、西南区 8 条
  新版：东北区 9 条、西南区 6 条
  另放入：地区缺失的事件、地区值为 "?" 的事件

运行：python -I verify_credentials.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

TMP_DB = tempfile.mkdtemp(prefix="cc10045-verify-")
os.environ["CC10045_DB_DIR"] = TMP_DB
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

from app.database import db  # noqa: E402
from app.pipeline import compute_aggregation, default_as_of  # noqa: E402
from app.services.aggregation_credentials import compare_regions, summarize_chart  # noqa: E402
from app.services.dashboard_builder import DashboardBuilderService  # noqa: E402
from app.services.drill_path import DrillPathService  # noqa: E402
from app.services.realtime_agg import RealtimeAggService, version_summary  # noqa: E402

FAILS: list[str] = []


def check(condition: bool, label: str, detail: object = "") -> None:
    if condition:
        print(f"  PASS  {label}")
    else:
        FAILS.append(label)
        print(f"  FAIL  {label}  -> {detail}")


def ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def add_events(region: object, count: int, start: datetime, step_seconds: int = 30, *, name: str = "下单") -> list[int]:
    ids = []
    attrs = {"user_id": "u_1"}
    if region is not _ABSENT:
        attrs["region"] = region
    for i in range(count):
        cursor = start + timedelta(seconds=i * step_seconds)
        ids.append(db.insert("events", {
            "event_name": name,
            "event_attrs": json.dumps(attrs, ensure_ascii=False),
            "trigger_condition": "",
            "source_module": "event_tracker",
            "occurred_at": ts(cursor),
        }))
    return ids


_ABSENT = object()

# ---------------------------------------------------------------------------
# 准备：一个按 region 分组的聚合窗口 + 两个引用它的图表
# ---------------------------------------------------------------------------
print("== 准备窗口与图表 ==")
window_id = db.insert("aggregation_windows", {
    "window_name": "区域订单窗口",
    "data_source_module": "all",
    "dimensions": json.dumps(["region"]),
    "agg_function": "COUNT",
    "window_type": "tumbling",
    "window_size_seconds": 600,
    "status": "已启用",
})
dashboard_id = db.insert("dashboards", {
    "dashboard_name": "区域对比看板",
    "chart_configs": json.dumps([
        {"type": "bar", "title": "东北区订单", "window": {
            "window_id": window_id, "region_dimension": "region", "region_value": "东北区"}},
        {"type": "bar", "title": "西南区订单", "window": {
            "window_id": window_id, "region_dimension": "region", "region_value": "西南区"}},
    ], ensure_ascii=False),
    "refresh_interval_seconds": 30,
    "data_scope": "region",
    "status": "已发布",
})

base = datetime(2026, 11, 20, 12, 0, 0, tzinfo=timezone.utc)
v1_as_of = ts(base)
v1_start = base - timedelta(seconds=600)

# 上版窗口（[base-600, base)）
ne_v1_ids = add_events("东北区", 7, v1_start + timedelta(seconds=5))
sw_v1_ids = add_events("西南区", 8, v1_start + timedelta(seconds=5))
missing_ids = add_events(_ABSENT, 2, v1_start + timedelta(seconds=5))
blank_ids = add_events("", 1, v1_start + timedelta(seconds=5))
qmark_ids = add_events("?", 3, v1_start + timedelta(seconds=5))

# 新版窗口（[base, base+600)）
v2_as_of = ts(base + timedelta(seconds=600))
v2_start = base
ne_v2_ids = add_events("东北区", 9, v2_start + timedelta(seconds=5))
sw_v2_ids = add_events("西南区", 6, v2_start + timedelta(seconds=5))

print("== 计算上版（版本 1，两边齐全） ==")
v1_results = compute_aggregation(db.fetchone("SELECT * FROM aggregation_windows WHERE id=?", [window_id]), as_of=v1_as_of)
v1_map = {r["dimension_key"]: r for r in v1_results}
check(v1_map["region=东北区"]["event_count"] == 7, "上版东北区 7 条", v1_map.get("region=东北区"))
check(v1_map["region=西南区"]["event_count"] == 8, "上版西南区 8 条", v1_map.get("region=西南区"))
check(v1_map["region=东北区"]["computation_version"] == 1, "上版版本号为 1")

svc = RealtimeAggService()
dash = DashboardBuilderService()
drill = DrillPathService()

north_chart = {"title": "东北区订单", "window": {"window_id": window_id, "region_dimension": "region", "region_value": "东北区"}}
south_chart = {"title": "西南区订单", "window": {"window_id": window_id, "region_dimension": "region", "region_value": "西南区"}}

cred_north_v1 = summarize_chart(north_chart)
cred_south_v1 = summarize_chart(south_chart)
check(cred_north_v1["ok"] and cred_north_v1["computation_version"] == 1, "东北区凭据落在版本 1")
check(cred_north_v1["contribution_count"] == 7, "东北区凭据贡献事件数 7", cred_north_v1["contribution_count"])
check(cred_south_v1["contribution_count"] == 8, "西南区凭据贡献事件数 8", cred_south_v1["contribution_count"])
check(
    cred_north_v1["window"]["window_name"] == "区域订单窗口"
    and cred_north_v1["dimensions"] == ["region"]
    and cred_north_v1["as_of"] == v1_as_of,
    "凭据包含窗口 / 维度 / 基准时刻",
    {k: cred_north_v1.get(k) for k in ("window", "dimensions", "as_of")},
)

# ---------------------------------------------------------------------------
print("== 东北区出新版，西南区仍在重算 ==")
ne_new = svc.run_region_version(window_id, region_dimension="region", region_value="东北区", as_of=v2_as_of)
check(ne_new["ok"], "东北区重算成功", ne_new.get("message"))
check(ne_new["computation_version"] == 2, "东北区拿到版本 2", ne_new["computation_version"])
check(ne_new["version_status"] == "部分完成", "版本 2 处于部分完成（西南区重算中）", ne_new["version_status"])
check(any("西南区" in k for k in ne_new["recalculating"]), "版本 2 里西南区标记重算中", ne_new["recalculating"])

cred_north_v2 = summarize_chart(north_chart)
cred_south_pending = summarize_chart(south_chart)
check(cred_north_v2["computation_version"] == 2 and cred_north_v2["contribution_count"] == 9,
      "东北区单独看版本 2（9 条）", (cred_north_v2["computation_version"], cred_north_v2["contribution_count"]))
check(cred_north_v2["pending_version"] is None, "东北区已在新版，无待重算提示")
check(cred_south_pending["computation_version"] == 1 and cred_south_pending["contribution_count"] == 8,
      "西南区仍取版本 1（8 条）", (cred_south_pending["computation_version"], cred_south_pending["contribution_count"]))
check(cred_south_pending["pending_version"] is not None
      and cred_south_pending["pending_version"]["status"] == "部分完成",
      "西南区凭据显示新版仍在重算", cred_south_pending.get("pending_version"))

# 单独打开东北区：按版本 2 下钻
ne_group = next(g for g in cred_north_v2["groups"] if g["dimension_values"].get("region") == "东北区")
ne_drill = drill.drill_from_credential(
    window_id=window_id, dimension_key=ne_group["dimension_key"],
    computation_version=cred_north_v2["computation_version"],
)
check(ne_drill["ok"], "东北区按凭据下钻成功", ne_drill.get("message"))
check(ne_drill["contribution_count"] == ne_drill["returned_count"] == 9,
      "东北区明细 9 条与汇总对上", (ne_drill["contribution_count"], ne_drill["returned_count"]))
check({e["id"] for e in ne_drill["events"]} == set(ne_v2_ids), "东北区明细来自新版事件")

# 并排比较：只能取两边都齐全的版本 1
compare = compare_regions(
    window_id=window_id, dimension_key="",
    left={"label": "东北区", "region_value": "东北区"},
    right={"label": "西南区", "region_value": "西南区"},
)
check(compare["ok"], "两地区并排比较可执行", compare.get("message"))
check(compare["computation_version"] == 1, "并排比较锁定共同齐全的版本 1", compare["computation_version"])
check(compare["left_latest_version"] == 2 and compare["right_latest_version"] == 1,
      "并排比较指出两边各自最新版本", (compare["left_latest_version"], compare["right_latest_version"]))
check(compare["version_aligned"] is False, "并排比较标记版本未对齐")
panel_counts = {p["side"]: p["summary"]["contribution_count"] for p in compare["panels"]}
check(panel_counts == {"left": 7, "right": 8}, "并排比较两面板分别为 7 / 8 条", panel_counts)

# ---------------------------------------------------------------------------
print("== 西南区补齐版本 2 ==")
sw_catchup = svc.run_region_version(
    window_id, region_dimension="region", region_value="西南区", as_of=v2_as_of, version=2,
)
check(sw_catchup["ok"], "西南区补齐成功", sw_catchup.get("message"))
check(sw_catchup["computation_version"] == 2, "西南区写入同一版本 2")

cred_north_final = summarize_chart(north_chart)
cred_south_final = summarize_chart(south_chart)
check(
    cred_north_final["computation_version"] == cred_south_final["computation_version"] == 2,
    "两图一起看到版本 2",
    (cred_north_final["computation_version"], cred_south_final["computation_version"]),
)
check(cred_north_final["contribution_count"] == 9 and cred_south_final["contribution_count"] == 6,
      "新版东北区 9 条、西南区 6 条",
      (cred_north_final["contribution_count"], cred_south_final["contribution_count"]))

compare_v2 = compare_regions(
    window_id=window_id, dimension_key="",
    left={"label": "东北区", "region_value": "东北区"},
    right={"label": "西南区", "region_value": "西南区"},
)
check(compare_v2["ok"] and compare_v2["computation_version"] == 2 and compare_v2["version_aligned"],
      "补齐后并排比较落在版本 2 且对齐", (compare_v2.get("computation_version"), compare_v2.get("version_aligned")))
v2_counts = {p["side"]: p["summary"]["contribution_count"] for p in compare_v2["panels"]}
check(v2_counts == {"left": 9, "right": 6}, "版本 2 并排 9 / 6", v2_counts)

# 旧图仍能回查取数依据
old_north = summarize_chart(north_chart, version=1)
old_south = summarize_chart(south_chart, version=1)
check(old_north["ok"] and old_north["contribution_count"] == 7, "东北区旧图仍可回查版本 1（7 条）")
check(old_south["ok"] and old_south["contribution_count"] == 8, "西南区旧图仍可回查版本 1（8 条）")
old_drill = drill.drill_from_credential(
    window_id=window_id,
    dimension_key=next(g for g in old_north["groups"] if g["dimension_values"].get("region") == "东北区")["dimension_key"],
    computation_version=1,
)
check(old_drill["returned_count"] == 7 and {e["id"] for e in old_drill["events"]} == set(ne_v1_ids),
      "旧版下钻取到的是旧版事件")

# 不允许用未完成版本下钻
partial = drill.drill_from_credential(
    window_id=window_id, dimension_key="region=西南区", computation_version=3,
)
check(not partial["ok"], "未登记版本不能下钻", partial.get("message"))

# ---------------------------------------------------------------------------
print("== 缺失组 vs 问号组 ==")
cred_all_v1 = summarize_chart({
    "title": "全区域", "window": {"window_id": window_id},
}, version=1)
check(cred_all_v1["ok"], "全区域凭据可读", cred_all_v1.get("message"))
missing_group = cred_all_v1["missing_group"]
qmark_groups = cred_all_v1["question_mark_groups"]
check(missing_group is not None, "存在明确标记的缺失组")
check(missing_group["dimension_values"].get("region") is None, "缺失组 region 取值为空")
check(missing_group["is_missing_group"] is True and missing_group["missing_dimensions"] == ["region"],
      "缺失组带缺失维度标记", missing_group)
check(len(qmark_groups) == 1, "存在 region 值为 ? 的普通分组", qmark_groups)
check(qmark_groups[0]["is_missing_group"] is False and qmark_groups[0]["dimension_values"]["region"] == "?",
      "问号组不是缺失组", qmark_groups[0])

missing_drill = drill.drill_from_credential(
    window_id=window_id, dimension_key=missing_group["dimension_key"], computation_version=1)
qmark_drill = drill.drill_from_credential(
    window_id=window_id, dimension_key=qmark_groups[0]["dimension_key"], computation_version=1)
missing_event_ids = {e["id"] for e in missing_drill["events"]}
qmark_event_ids = {e["id"] for e in qmark_drill["events"]}
check(missing_drill["is_missing_group"] and not qmark_drill["is_missing_group"],
      "下钻结果分别标注缺失组 / 问号组")
check(missing_event_ids and qmark_event_ids and not (missing_event_ids & qmark_event_ids),
      "缺失组与问号组样本不重叠", (sorted(missing_event_ids), sorted(qmark_event_ids)))
check(set(missing_ids) | set(blank_ids) != set(), "缺失组样本来自缺键与空值事件")
check(missing_event_ids <= set(missing_ids) | set(blank_ids),
      "缺失组样本只包含缺键 / 空值事件", sorted(missing_event_ids))
check(qmark_event_ids == set(qmark_ids), "问号组样本只包含 region=? 的事件", sorted(qmark_event_ids))
check(missing_drill["returned_count"] == missing_drill["contribution_count"] == 3,
      "缺失组明细条数与汇总一致（2 缺键 + 1 空值）", missing_drill["returned_count"])
check(qmark_drill["returned_count"] == 3, "问号组明细 3 条", qmark_drill["returned_count"])

# ---------------------------------------------------------------------------
print("== 仪表盘接口层 ==")
creds = dash.chart_credentials(dashboard_id)
check(creds["ok"] and len(creds["charts"]) == 2, "仪表盘返回两张图的凭据")
check(all(c["contribution_count"] in (9, 6) for c in creds["charts"]), "接口层两图都取版本 2")
one = dash.chart_credential(dashboard_id, 0)
check(one["ok"] and one["title"] == "东北区订单", "单图凭据可取")
cmp_payload = dash.compare_chart_regions(
    dashboard_id, 0, left_region="东北区", right_region="西南区",
)
check(cmp_payload["ok"] and cmp_payload["computation_version"] == 2, "仪表盘并排比较落在版本 2")

print()
if FAILS:
    print(f"FAILED {len(FAILS)} 项：")
    for item in FAILS:
        print(" -", item)
    sys.exit(1)
print("全部验证通过")
