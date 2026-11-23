/** 纯前端数据层的公共类型：与全栈版后端返回的结构保持一致，换回后端时页面不用改。 */

export type EntryRow = {
  id: number
  status: string
  pending: boolean
  abnormal: boolean
  [field: string]: string | number | boolean
}

export type ModuleMeta = {
  key: string
  name: string
  entity: string
  desc: string
  fields: string[]
  statuses: string[]
  actions: string[]
  actionTargets: Record<string, string>
  metrics: string[]
}

export type PageResult = {
  items: EntryRow[]
  total: number
  page: number
  size: number
}

export type ActionResult = {
  ok: boolean
  message: string
}

export type OverviewResult = {
  cards: { label: string; value: number }[]
  modules: { name: string; created: number; pending: number; abnormal: number }[]
}

/** 汇总凭据里的一个维度分组：贡献事件数与下钻明细条数应一致。 */
export type CredentialGroup = {
  result_id: number
  dimension_key: string
  dimension_label: string
  dimension_values: Record<string, string | null>
  missing_dimensions: string[]
  is_missing_group: boolean
  computed_value: number
  event_count: number
  contribution_count: number
  computation_version: number
  window_start: string
  window_end: string
}

/** 图表查询实时聚合数据时的取数依据。 */
export type ChartCredential = {
  ok: boolean
  message?: string
  chart_index?: number
  title: string
  dashboard_id?: number
  window: {
    window_id: number
    window_name: string
    window_type: string
    window_size_seconds: number
    agg_function: string
  }
  dimensions: string[]
  region_dimension: string | null
  region_value: string | null
  region_group: string | null
  computation_version: number | null
  version_status: string
  latest_window_version: number | null
  pending_version: {
    version: number
    status: string
    recalculating: string[]
  } | null
  as_of: string | null
  window_start: string | null
  window_end: string | null
  contribution_count: number
  value_total?: number
  groups: CredentialGroup[]
  missing_group: CredentialGroup | null
  question_mark_groups: CredentialGroup[]
}

export type DashboardCredentials = {
  ok: boolean
  message?: string
  dashboard_id: number
  dashboard_name: string
  requested_version: number | null
  charts: ChartCredential[]
}

export type DrillEvent = {
  id: number
  event_name: string
  occurred_at: string
  event_attrs: Record<string, unknown>
}

export type DrillResult = {
  ok: boolean
  message?: string
  window_id: number
  dimension_key: string
  dimension_label: string
  dimension_values: Record<string, string | null>
  missing_dimensions: string[]
  is_missing_group: boolean
  computation_version: number
  contribution_count: number
  returned_count: number
  matched: boolean
  truncated: boolean
  events: DrillEvent[]
}

export type RegionComparePanel = {
  side: 'left' | 'right'
  label: string
  region_value: string
  latest_version: number
  version_status: string
  summary: ChartCredential
}

export type RegionCompareResult = {
  ok: boolean
  message?: string
  window_id: number
  dimension_key: string
  region_dimension: string
  computation_version: number
  left_latest_version: number | null
  right_latest_version: number | null
  version_aligned: boolean
  panels: RegionComparePanel[]
}
