import { request } from './client'
import { moduleMeta } from '@/data/modules'
import type {
  ActionResult,
  ChartCredential,
  DashboardCredentials,
  DrillResult,
  EntryRow,
  OverviewResult,
  PageResult,
  RegionCompareResult,
} from '@/data/types'

export { moduleMeta }

export async function listEntries(
  key: string,
  filters: Record<string, string> = {},
  page = 1,
  size = 20,
): Promise<PageResult> {
  const query = new URLSearchParams({ ...filters, page: String(page), size: String(size) }).toString()
  const response = await request(`/api/${key}?${query}`)
  if (!response.ok) throw new Error('业务数据读取失败')
  return await response.json()
}

export async function createEntry(
  key: string,
  values: Record<string, string>,
): Promise<ActionResult> {
  const response = await request(`/api/${key}`, {
    method: 'POST',
    body: JSON.stringify({ values }),
  })
  return await response.json()
}

export async function runAction(
  key: string,
  id: number,
  action: string,
): Promise<ActionResult> {
  const response = await request(`/api/${key}/${id}/actions`, {
    method: 'POST',
    body: JSON.stringify({ action }),
  })
  return await response.json()
}

export async function downloadEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<void> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}/export?${query}`)
  if (!response.ok) throw new Error('业务数据导出失败')
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${key}.csv`
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
}

export async function loadOverview(): Promise<OverviewResult> {
  const response = await request('/api/overview')
  if (!response.ok) throw new Error('运营概览读取失败')
  return await response.json()
}

// ---------------------------------------------------------------
// 汇总凭据 / 下钻 / 并排比较
// ---------------------------------------------------------------

export async function loadDashboardCredentials(
  dashboardId: number,
  version?: number | null,
  allGroups = false,
): Promise<DashboardCredentials> {
  const params = new URLSearchParams()
  if (version) params.set('version', String(version))
  if (allGroups) params.set('all_groups', 'true')
  const query = params.toString() ? `?${params.toString()}` : ''
  const response = await request(`/api/dashboard_builder/${dashboardId}/credentials${query}`)
  if (!response.ok) throw new Error('汇总凭据读取失败')
  return await response.json()
}

export async function loadChartCredential(
  dashboardId: number,
  chartIndex: number,
  version?: number | null,
): Promise<ChartCredential> {
  const query = version ? `?version=${version}` : ''
  const response = await request(
    `/api/dashboard_builder/${dashboardId}/charts/${chartIndex}/credential${query}`,
  )
  if (!response.ok) throw new Error('图表取数依据读取失败')
  return await response.json()
}

export async function compareChartRegions(
  dashboardId: number,
  chartIndex: number,
  left: string,
  right: string,
  regionDimension = 'region',
): Promise<RegionCompareResult> {
  const query = new URLSearchParams({
    left,
    right,
    region_dimension: regionDimension,
  }).toString()
  const response = await request(
    `/api/dashboard_builder/${dashboardId}/charts/${chartIndex}/compare?${query}`,
  )
  if (!response.ok) throw new Error('并排比较读取失败')
  return await response.json()
}

export async function drillFromCredential(payload: {
  window_id: number
  dimension_key: string
  computation_version: number
  limit?: number
}): Promise<DrillResult> {
  const response = await request('/api/drill_path/drill', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
  if (!response.ok) throw new Error('贡献明细读取失败')
  return await response.json()
}
