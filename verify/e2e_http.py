"""HTTP 端到端：走真实路由验证汇总凭据 / 版本化下钻 / 并排比较 / 缺失组。

前置：后端已用 CC10045_DB_DIR 指向临时库启动在 BASE。
事件没有按地区摄入的接口，故事件用 sqlite 直插同一个库，其余全部走 HTTP。
运行：python -I e2e_http.py http://127.0.0.1:8119 /tmp/cc10045-e2e/cc10045.db
"""
from __future__ import annotations

import json
import sqlite3
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

L = urllib.parse.quote("东北区")
S = urllib.parse.quote("西南区")

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8119"
DB_PATH = sys.argv[2] if len(sys.argv) > 2 else "/tmp/cc10045-e2e/cc10045.db"

FAILS: list[str] = []


def check(ok: bool, label: str, detail: object = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + ("" if ok else f"  -> {detail}"))
    if not ok:
        FAILS.append(label)


def call(method: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return {"ok": False, "http_status": exc.code, "detail": exc.read().decode()}


def insert_events(region, count: int, window_end: str, *, absent: bool = False,
                  minutes_before_end: int = 5) -> list[int]:
    end = datetime.strptime(window_end, "%Y-%m-%dT%H:%M:%S")
    conn = sqlite3.connect(DB_PATH, timeout=10)
    ids = []
    for i in range(count):
        attrs = {"user_id": "u_1"}
        if not absent:
            attrs["region"] = region
        occurred = (end - timedelta(minutes=minutes_before_end) + timedelta(seconds=i)).strftime("%Y-%m-%dT%H:%M:%S")
        cur = conn.execute(
            "INSERT INTO events(event_name,event_attrs,trigger_condition,source_module,occurred_at) "
            "VALUES(?,?,?,?,?)",
            ("下单", json.dumps(attrs, ensure_ascii=False), "", "event_tracker", occurred),
        )
        ids.append(cur.lastrowid)
    conn.commit()
    conn.close()
    return ids


print("== 建专用小窗口与区域看板 ==")
window = call("POST", "/api/realtime_agg", {"values": {
    "窗口名称": "E2E 区域窗口",
    "数据源": "all",
    "聚合维度": '["region"]',
    "聚合函数": "COUNT",
    "窗口大小": "300",
}})
check(window.get("ok"), "创建聚合窗口", window)
window_id = window["entry"]["id"]
call("POST", f"/api/realtime_agg/{window_id}/actions", {"action": "启用聚合"})
charts = [
    {"type": "bar", "title": "东北区订单",
     "window": {"window_id": window_id, "region_dimension": "region", "region_value": "东北区"}},
    {"type": "bar", "title": "西南区订单",
     "window": {"window_id": window_id, "region_dimension": "region", "region_value": "西南区"}},
]
dash = call("POST", "/api/dashboard_builder", {"values": {
    "仪表盘名称": "E2E 区域看板",
    "包含图表": json.dumps(charts, ensure_ascii=False),
    "刷新间隔": "30",
    "数据范围": "region",
    "共享状态": "team",
}})
check(dash.get("ok"), "创建区域看板", dash)
dash = dash["entry"]
check(len(charts) == 2, "看板有两张地区图表")

# 5 分钟窗口，两版时间窗完全不相交：
#   上版窗口截止 as_of；新版窗口截止 as_of_v2 = as_of - 10 分钟。
now = datetime.now(timezone.utc).replace(microsecond=0)
as_of = now.strftime("%Y-%m-%dT%H:%M:%S")
as_of_v2 = (now - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%S")

print("== 上版事件：东北 7 / 西南 8 / 缺失 3 / 问号 3 ==")
ne_ids = insert_events("东北区", 7, as_of, minutes_before_end=1)
sw_ids = insert_events("西南区", 8, as_of, minutes_before_end=1)
missing_ids = insert_events(None, 2, as_of, absent=True, minutes_before_end=1)
blank_ids = insert_events("", 1, as_of, minutes_before_end=1)
qmark_ids = insert_events("?", 3, as_of, minutes_before_end=1)

# 整窗计算一次，作为上版（版本 1，两边齐全，含缺失组与问号组）
run = call("POST", f"/api/pipeline/aggregation/{window_id}/run", {"as_of": as_of})
check(run.get("ok"), "上版整窗计算成功", run)

versions = call("GET", f"/api/pipeline/aggregation/{window_id}/versions")
v1 = versions["versions"][-1]["version"] if versions.get("versions") else 1
check(versions["versions"][-1]["status"] == "已完成", "上版整版完成", versions["versions"][-1])

creds = call("GET", f"/api/dashboard_builder/{dash['id']}/credentials")
check(creds.get("ok") and len(creds["charts"]) == 2, "凭据接口返回两张图", creds)
counts = {c["title"]: c["contribution_count"] for c in creds["charts"]}
check(counts.get("东北区订单") == 7 and counts.get("西南区订单") == 8, "上版 7 / 8", counts)
check(all(c["computation_version"] == v1 for c in creds["charts"]), "两图都在同一上版", v1)

print("== 东北区出新版（新增 2 条，7 → 9） ==")
# 新版窗口与上版不相交，只含新追加的事件
insert_events("东北区", 9, as_of_v2, minutes_before_end=1)
new = call("POST", f"/api/pipeline/aggregation/{window_id}/run-region", {
    "region_value": "东北区", "as_of": as_of_v2,
})
check(new.get("ok"), "东北区重算成功", new)
v2 = new["computation_version"]
check(v2 == v1 + 1, "东北区拿到新版本", v2)
check(new["version_status"] == "部分完成", "新版部分完成", new["version_status"])

north = call("GET", f"/api/dashboard_builder/{dash['id']}/charts/0/credential")
south = call("GET", f"/api/dashboard_builder/{dash['id']}/charts/1/credential")
check(north["computation_version"] == v2 and north["contribution_count"] == 9, "东北区单独看新版 9 条",
      (north["computation_version"], north["contribution_count"]))
check(south["computation_version"] == v1 and south["contribution_count"] == 8, "西南区仍在上版 8 条",
      (south["computation_version"], south["contribution_count"]))
check(south.get("pending_version") is not None, "西南区提示新版仍在重算", south.get("pending_version"))

north_group = next(g for g in north["groups"] if g["dimension_values"].get("region") == "东北区")
drill = call("POST", "/api/drill_path/drill", {
    "window_id": window_id,
    "dimension_key": north_group["dimension_key"],
    "computation_version": v2,
})
check(drill.get("ok") and drill["returned_count"] == 9, "东北区明细 9 条", drill.get("returned_count"))
check(drill["returned_count"] == north["contribution_count"], "明细与汇总条数一致")

cmp = call("GET", f"/api/dashboard_builder/{dash['id']}/charts/0/compare"
                   f"?left={L}&right={S}&region_dimension=region")
check(cmp.get("ok"), "并排比较可用", cmp)
check(cmp["computation_version"] == v1, "并排比较锁定共同版本", cmp["computation_version"])
check(cmp["version_aligned"] is False, "并排比较标记未对齐")
check({p["side"]: p["summary"]["contribution_count"] for p in cmp["panels"]} == {"left": 7, "right": 8},
      "并排面板 7 / 8", cmp["panels"])

print("== 西南区补齐新版 ==")
insert_events("西南区", 6, as_of_v2, minutes_before_end=1)
fill2 = call("POST", f"/api/pipeline/aggregation/{window_id}/run-region", {
    "region_value": "西南区", "as_of": as_of_v2, "version": v2,
})
check(fill2.get("ok"), "西南区补齐版本 " + str(v2), fill2)
north2 = call("GET", f"/api/dashboard_builder/{dash['id']}/charts/0/credential")
south2 = call("GET", f"/api/dashboard_builder/{dash['id']}/charts/1/credential")
check(north2["computation_version"] == south2["computation_version"] == v2, "两图一起切到新版")
check(north2["contribution_count"] == 9 and south2["contribution_count"] == 6, "新版 9 / 6",
      (north2["contribution_count"], south2["contribution_count"]))
cmp2 = call("GET", f"/api/dashboard_builder/{dash['id']}/charts/0/compare"
                   f"?left={L}&right={S}&region_dimension=region")
check(cmp2.get("ok") and cmp2["version_aligned"] and cmp2["computation_version"] == v2,
      "补齐后并排对齐在新版", (cmp2.get("computation_version"), cmp2.get("version_aligned")))

old = call("GET", f"/api/dashboard_builder/{dash['id']}/charts/0/credential?version={v1}")
check(old.get("ok") and old["contribution_count"] == 7, "旧图仍可回查上版 7 条",
      (old.get("contribution_count"), old.get("message")))
old_drill = call("POST", "/api/drill_path/drill", {
    "window_id": window_id, "dimension_key": north_group["dimension_key"], "computation_version": v1,
})
check(old_drill.get("ok") and old_drill["returned_count"] == 7, "旧版下钻 7 条", old_drill.get("returned_count"))
bad = call("POST", "/api/drill_path/drill", {
    "window_id": window_id, "dimension_key": "region=西南区", "computation_version": v2 + 5,
})
check(not bad.get("ok"), "不存在版本拒绝下钻", bad)

print("== 缺失组 vs 问号组 ==")
all_v = call("GET", f"/api/dashboard_builder/{dash['id']}/charts/0/credential?version={v1}")
# 需要全区域视图：钻取 region 分组
cred_all = call("GET", f"/api/pipeline/aggregation/{window_id}/versions")
# 用版本 v1 的东北区凭据拿不到缺失组，改用聚合结果直查
conn = sqlite3.connect(DB_PATH)
rows = conn.execute(
    "SELECT dimension_key, missing_dimensions, event_count FROM aggregation_results "
    "WHERE window_id=? AND computation_version=? ORDER BY dimension_key",
    (window_id, v1),
).fetchall()
conn.close()
by_key = {r[0]: r for r in rows}
missing_key = "region=__MISSING__"
qmark_key = "region=?"
check(missing_key in by_key, "存在缺失分组", list(by_key))
check(qmark_key in by_key, "存在问号分组", list(by_key))
check(json.loads(by_key[missing_key][1]) == ["region"], "缺失分组带缺失维度标记", by_key[missing_key])
check(json.loads(by_key[qmark_key][1]) == [], "问号分组不是缺失组", by_key[qmark_key])

m_drill = call("POST", "/api/drill_path/drill", {
    "window_id": window_id, "dimension_key": missing_key, "computation_version": v1})
q_drill = call("POST", "/api/drill_path/drill", {
    "window_id": window_id, "dimension_key": qmark_key, "computation_version": v1})
check(m_drill["returned_count"] == m_drill["contribution_count"],
      "缺失组明细条数与汇总一致", (m_drill["returned_count"], m_drill["contribution_count"]))
check(m_drill["returned_count"] >= 3, "缺失组至少包含新增的 3 条", m_drill["returned_count"])
check(q_drill["returned_count"] == 3, "问号组 3 条", q_drill["returned_count"])
check(m_drill["is_missing_group"] and not q_drill["is_missing_group"], "下钻分别标注两组")
m_ids = {e["id"] for e in m_drill["events"]}
q_ids = {e["id"] for e in q_drill["events"]}
check(not (m_ids & q_ids), "两组样本不重叠")
check(set(missing_ids) | set(blank_ids) <= m_ids, "新增的缺键/空值事件都在缺失组", sorted(m_ids))
# 缺失组里的每个样本，region 属性都确实取不到值
check(all(
    e["event_attrs"].get("region") in (None, "")
    for e in m_drill["events"]
), "缺失组没有混入真实填了 region 的事件")
check(q_ids == set(qmark_ids), "问号组只含 region=? 事件", sorted(q_ids))

print("== 地区过滤图 + 全部分组视图 ==")
region_view = call("GET", f"/api/dashboard_builder/{dash['id']}/charts/0/credential?version={v1}")
check(all(g["dimension_values"].get("region") == "东北区" for g in region_view["groups"]),
      "地区图凭据只含本地区分组", [g["dimension_values"] for g in region_view["groups"]])
all_view = call("GET", f"/api/dashboard_builder/{dash['id']}/charts/0/credential"
                       f"?version={v1}&all_groups=true")
keys = {g["dimension_key"] for g in all_view["groups"]}
check(missing_key in keys and qmark_key in keys, "全部分组视图同时列出缺失组与问号组", sorted(keys))
m_group = next(g for g in all_view["groups"] if g["dimension_key"] == missing_key)
q_group = next(g for g in all_view["groups"] if g["dimension_key"] == qmark_key)
check(m_group["is_missing_group"] and not q_group["is_missing_group"], "两组标记不同")
check(m_group["contribution_count"] == m_drill["returned_count"],
      "缺失组凭据条数等于明细条数", (m_group["contribution_count"], m_drill["returned_count"]))
check(q_group["contribution_count"] == q_drill["returned_count"],
      "问号组凭据条数等于明细条数", (q_group["contribution_count"], q_drill["returned_count"]))
check(m_group["dimension_values"]["region"] is None and q_group["dimension_values"]["region"] == "?",
      "两组的 region 取值可区分", (m_group["dimension_values"], q_group["dimension_values"]))

print()
if FAILS:
    print(f"FAILED {len(FAILS)} 项：")
    for f in FAILS:
        print(" -", f)
    sys.exit(1)
print("HTTP 端到端全部通过")
