<template>
  <section class="credential-panel" data-testid="credential-panel">
    <header class="credential-head">
      <div>
        <h3>汇总凭据 · {{ dashboard?.dashboard_name ?? '仪表盘' }}</h3>
        <p class="credential-hint">
          每张图表展示实时聚合所用窗口、维度、计算版本与贡献事件数；展开分组可下钻核对明细条数。
        </p>
      </div>
      <div class="credential-toggles">
        <label class="credential-version">
          <span>回查版本</span>
          <select :value="versionFilter ?? ''" data-testid="credential-version" @change="onVersionChange">
            <option value="">跟随各图最新已完成版本</option>
            <option v-for="version in knownVersions" :key="version" :value="version">
              版本 {{ version }}
            </option>
          </select>
        </label>
        <label class="credential-checkbox">
          <input
            v-model="allGroups"
            type="checkbox"
            data-testid="credential-all-groups"
          />
          <span>显示全部分组（含缺失组与取值为 ? 的分组）</span>
        </label>
      </div>
    </header>

    <p v-if="errorMessage" class="error-text">{{ errorMessage }}</p>
    <p v-if="loading" class="credential-hint">正在读取汇总凭据…</p>

    <article
      v-for="chart in charts"
      :key="`${chart.chart_index}-${chart.computation_version}`"
      class="credential-chart"
      :data-testid="`credential-chart-${chart.chart_index}`"
    >
      <div class="credential-chart-head">
        <strong>{{ chart.title }}</strong>
        <span v-if="!chart.ok" class="badge badge-warn">{{ chart.message ?? '暂无凭据' }}</span>
        <template v-else>
          <span class="badge">窗口 {{ chart.window.window_name }}</span>
          <span class="badge">维度 {{ chart.dimensions.join('、') || '全部' }}</span>
          <span class="badge">版本 {{ chart.computation_version }}</span>
          <span class="badge">贡献事件 {{ chart.contribution_count }} 条</span>
          <span
            class="badge"
            :class="statusClass(chart.version_status)"
            :data-testid="`credential-status-${chart.chart_index}`"
          >
            {{ chart.version_status }}
          </span>
        </template>
      </div>

      <dl v-if="chart.ok" class="credential-meta">
        <div><dt>聚合函数</dt><dd>{{ chart.window.agg_function }}</dd></div>
        <div><dt>窗口大小</dt><dd>{{ chart.window.window_size_seconds }} 秒</dd></div>
        <div><dt>聚合区间</dt><dd>{{ chart.window_start }} ~ {{ chart.window_end }}</dd></div>
        <div><dt>基准时刻</dt><dd>{{ chart.as_of }}</dd></div>
      </dl>

      <p v-if="chart.ok && chart.pending_version" class="pending-note" data-testid="pending-version">
        窗口已有版本 {{ chart.pending_version.version }}（{{ chart.pending_version.status }}），
        该地区仍取版本 {{ chart.computation_version }}；
        待 {{ chart.pending_version.recalculating.length }} 个分组完成后可一起切到新版。
      </p>

      <div v-if="chart.ok && chart.groups.length" class="credential-groups">
        <div
          v-for="group in chart.groups"
          :key="group.result_id"
          class="credential-group"
          :class="{ 'is-missing': group.is_missing_group, 'is-question': isQuestionGroup(group) }"
          :data-testid="`group-${chart.chart_index}-${group.dimension_key}`"
        >
          <button class="link group-toggle" type="button" @click="toggleGroup(chart, group)">
            <span class="group-label">
              {{ group.dimension_label }}
              <em v-if="group.is_missing_group" class="missing-tag">缺失组</em>
              <em v-else-if="isQuestionGroup(group)" class="question-tag">问号取值</em>
            </span>
            <span class="group-figures">
              值 {{ group.computed_value }} · 汇总 {{ group.contribution_count }} 条
            </span>
          </button>
          <span v-if="isExpanded(chart, group)" class="drill-summary" :data-testid="`drill-count-${group.dimension_key}`">
            <template v-if="drillFor(chart, group)">
              明细 {{ drillFor(chart, group)!.returned_count }} 条
              <em :class="drillFor(chart, group)!.matched ? 'ok-text' : 'error-text'">
                {{ drillFor(chart, group)!.matched ? '与汇总一致' : '与汇总不一致' }}
              </em>
            </template>
            <span v-else>正在读取明细…</span>
          </span>

          <table
            v-if="isExpanded(chart, group) && drillFor(chart, group)?.events.length"
            class="data-table drill-table"
          >
            <thead>
              <tr><th>事件 ID</th><th>事件名称</th><th>发生时间</th><th>维度属性</th></tr>
            </thead>
            <tbody>
              <tr v-for="event in drillFor(chart, group)!.events" :key="event.id">
                <td>{{ event.id }}</td>
                <td>{{ event.event_name }}</td>
                <td>{{ event.occurred_at }}</td>
                <td>{{ formatAttrs(event.event_attrs) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <div v-if="chart.ok && chart.dimensions.includes(regionDimension)" class="credential-compare">
        <div class="compare-controls">
          <label>
            <span>地区 A</span>
            <input v-model="compareLeft" data-testid="compare-left" />
          </label>
          <label>
            <span>地区 B</span>
            <input v-model="compareRight" data-testid="compare-right" />
          </label>
          <button class="btn" type="button" data-testid="compare-run" @click="runCompare(chart)">
            并排比较
          </button>
        </div>

        <div v-if="compare && compare.ok" class="compare-panels" data-testid="compare-panels">
          <p class="compare-note" :class="{ 'error-text': !compare.version_aligned }">
            并排比较锁定共同齐全的版本 {{ compare.computation_version }}；
            地区 A 最新 {{ compare.left_latest_version ?? '—' }}，地区 B 最新
            {{ compare.right_latest_version ?? '—' }}
            {{ compare.version_aligned ? '（两边已对齐）' : '（两边未对齐，取双方都齐全的版本）' }}。
          </p>
          <div v-for="panel in compare.panels" :key="panel.side" class="compare-panel">
            <h4>{{ panel.label }}</h4>
            <p>版本 {{ compare.computation_version }} · {{ panel.version_status }}</p>
            <p class="compare-value">
              汇总值 {{ panel.summary.value_total }} · 贡献事件 {{ panel.summary.contribution_count }} 条
            </p>
            <button class="link" type="button" @click="drillPanel(panel)">
              查看该地区明细
            </button>
            <span
              v-if="panel.summary.groups[0] && drillFor(panel.summary, panel.summary.groups[0])"
              class="drill-summary"
            >
              明细 {{ drillFor(panel.summary, panel.summary.groups[0])!.returned_count }} 条
            </span>
          </div>
        </div>
        <p v-else-if="compare && !compare.ok" class="error-text" data-testid="compare-blocked">
          {{ compare.message }}
          （地区 A 最新 {{ compare.left_latest_version ?? '—' }}，地区 B 最新
          {{ compare.right_latest_version ?? '—' }}）
        </p>
      </div>
    </article>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'

import {
  compareChartRegions,
  drillFromCredential,
  loadDashboardCredentials,
} from '@/api/backend-service'
import type {
  ChartCredential,
  CredentialGroup,
  DashboardCredentials,
  DrillResult,
  RegionCompareResult,
} from '@/data/types'

const props = defineProps<{ dashboardId: number }>()

const dashboard = ref<DashboardCredentials | null>(null)
const loading = ref(false)
const errorMessage = ref('')
const versionFilter = ref<number | null>(null)
const allGroups = ref(false)
const expanded = ref<Set<string>>(new Set())
const drills = ref<Record<string, DrillResult>>({})
const compare = ref<RegionCompareResult | null>(null)
const compareLeft = ref('东北区')
const compareRight = ref('西南区')

const charts = computed(() => dashboard.value?.charts ?? [])
const regionDimension = computed(() => {
  const chart = charts.value.find((item) => item.ok && item.region_dimension)
  return chart?.region_dimension ?? 'region'
})
// 版本下拉来自一次未过滤的读数：按版本回查后列表不能缩水，否则旧版就选不回去了。
const versionPool = ref<Set<number>>(new Set())
const knownVersions = computed(() => [...versionPool.value].sort((a, b) => b - a))

function rememberVersions(payload: DashboardCredentials) {
  const next = new Set(versionPool.value)
  for (const chart of payload.charts) {
    if (chart.computation_version) next.add(chart.computation_version)
    if (chart.latest_window_version) next.add(chart.latest_window_version)
    if (chart.pending_version) next.add(chart.pending_version.version)
  }
  versionPool.value = next
}

function keyFor(chart: ChartCredential, group: CredentialGroup): string {
  return `${chart.chart_index}:${chart.computation_version}:${group.dimension_key}`
}

function isExpanded(chart: ChartCredential, group: CredentialGroup): boolean {
  return expanded.value.has(keyFor(chart, group))
}

function drillFor(chart: ChartCredential, group: CredentialGroup): DrillResult | null {
  return drills.value[keyFor(chart, group)] ?? null
}

async function toggleGroup(chart: ChartCredential, group: CredentialGroup) {
  const key = keyFor(chart, group)
  if (expanded.value.has(key)) {
    expanded.value.delete(key)
    expanded.value = new Set(expanded.value)
    return
  }
  expanded.value = new Set([...expanded.value, key])
  if (drills.value[key] || !chart.computation_version) return
  try {
    const result = await drillFromCredential({
      window_id: chart.window.window_id,
      dimension_key: group.dimension_key,
      computation_version: chart.computation_version,
    })
    drills.value = { ...drills.value, [key]: result }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '明细读取失败'
  }
}

async function drillPanel(panel: RegionComparePanelLike) {
  const group = panel.summary.groups[0]
  if (group) await toggleGroup(panel.summary, group)
}

type RegionComparePanelLike = RegionCompareResult['panels'][number]

function statusClass(status: string): string {
  if (status === '已完成') return 'badge-ok'
  if (status === '部分完成') return 'badge-warn'
  return 'badge-pending'
}

// 事件里真的填了 "?" 的分组：它是有取值的普通分组，不是缺失组。
function isQuestionGroup(group: CredentialGroup): boolean {
  return !group.is_missing_group
    && Object.values(group.dimension_values).some((value) => value === '?')
}

function formatAttrs(attrs: Record<string, unknown>): string {
  return Object.entries(attrs)
    .map(([key, value]) => `${key}=${value === null ? '（空）' : String(value)}`)
    .join('，')
}

async function runCompare(chart: ChartCredential) {
  errorMessage.value = ''
  compare.value = null
  try {
    compare.value = await compareChartRegions(
      props.dashboardId,
      chart.chart_index ?? 0,
      compareLeft.value,
      compareRight.value,
      chart.region_dimension ?? 'region',
    )
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '并排比较失败'
  }
}

function onVersionChange(event: Event) {
  const raw = (event.target as HTMLSelectElement).value
  versionFilter.value = raw ? Number(raw) : null
}

async function reload() {
  loading.value = true
  errorMessage.value = ''
  expanded.value = new Set()
  drills.value = {}
  compare.value = null
  try {
    if (versionFilter.value === null) {
      // 未锁定版本时先取一次最新读数，把出现过的版本都记进下拉。
      const latest = await loadDashboardCredentials(props.dashboardId, null, allGroups.value)
      rememberVersions(latest)
      dashboard.value = latest
    } else {
      dashboard.value = await loadDashboardCredentials(
        props.dashboardId, versionFilter.value, allGroups.value)
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '汇总凭据读取失败'
  } finally {
    loading.value = false
  }
}

watch(versionFilter, reload)
watch(allGroups, reload)
onMounted(reload)
</script>
