export interface RunSummaryItem {
  id: string
  platform: string
  status: string
  processed_count: number
  pause_reason_codes: string[]
}

const platformOrder = ['BOSS', 'MAIMAI', 'TELEGRAM']
const reconnectablePauseReasons = new Set([
  'MESSAGE_DISCOVERY_UNAVAILABLE',
  'JOB_DISCOVERY_UNAVAILABLE',
  'RECOMMENDATION_DISCOVERY_UNAVAILABLE',
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

export function agentStatusText(runs: RunSummaryItem[]) {
  const currentRuns = activeRuns(runs)
  if (currentRuns.length === 0) return '未启动'
  if (currentRuns.every((item) => item.status === 'RUNNING')) {
    return currentRuns.length === 1 ? 'RUNNING' : `${currentRuns.length} 个平台运行中`
  }
  return currentRuns.some((item) => item.status === 'RUNNING') ? '部分运行' : '已暂停'
}

export function canReconnectRun(
  run: Pick<RunSummaryItem, 'status' | 'pause_reason_codes'>,
) {
  return run.status === 'PAUSED'
    && run.pause_reason_codes.some((reason) => reconnectablePauseReasons.has(reason))
}
