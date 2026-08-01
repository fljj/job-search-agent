export interface RunSummaryItem {
  id: string
  platform: string
  status: string
  processed_count: number
  pause_reason_codes: string[]
}

const platformOrder = ['BOSS', 'MAIMAI', 'LIEPIN']
const reconnectablePauseReasons = new Set([
  'MESSAGE_DISCOVERY_UNAVAILABLE',
  'JOB_DISCOVERY_UNAVAILABLE',
  'RECOMMENDATION_DISCOVERY_UNAVAILABLE',
  'RESULT_NOT_OBSERVED',
])

export function activeRuns<T extends RunSummaryItem>(runs: T[]) {
  return runs
    .filter((item) => ['RUNNING', 'PAUSED'].includes(item.status))
    .sort((left, right) => {
      const leftOrder = platformOrder.indexOf(left.platform)
      const rightOrder = platformOrder.indexOf(right.platform)
      return (leftOrder < 0 ? platformOrder.length : leftOrder)
        - (rightOrder < 0 ? platformOrder.length : rightOrder)
    })
}

export function processedJobAndMessageCount(runs: RunSummaryItem[]) {
  return activeRuns(runs)
    .filter((item) => item.platform !== 'MAIMAI')
    .reduce((sum, item) => sum + item.processed_count, 0)
}

export function agentStatusText(
  runs: RunSummaryItem[],
  workerRunning = true,
) {
  const currentRuns = activeRuns(runs)
  if (currentRuns.length === 0) return '未启动'
  if (!workerRunning) return `Worker 未运行（${currentRuns.length} 个平台已启用）`
  if (currentRuns.every((item) => item.status === 'RUNNING')) {
    return currentRuns.length === 1 ? 'RUNNING' : `${currentRuns.length} 个平台运行中`
  }
  return currentRuns.some((item) => item.status === 'RUNNING') ? '部分运行' : '已暂停'
}

export function runStatusText(run: RunSummaryItem, workerRunning: boolean) {
  return run.status === 'RUNNING' && !workerRunning
    ? '已启用，等待 Worker'
    : run.status
}

export function canReconnectRun(
  run: Pick<RunSummaryItem, 'status' | 'pause_reason_codes'>,
) {
  return run.status === 'PAUSED'
    && run.pause_reason_codes.some((reason) => reconnectablePauseReasons.has(reason))
}

export function canResumeRun(
  run: Pick<RunSummaryItem, 'status' | 'pause_reason_codes'>,
) {
  return run.status === 'PAUSED' && run.pause_reason_codes.includes('USER_PAUSED')
}
