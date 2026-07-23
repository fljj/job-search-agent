import { describe, expect, it } from 'vitest'
import { statusColor } from './pages/automation-status'
import { activeWorkers, workerStatusText } from './pages/worker-status'

describe('Agent 控制台状态展示', () => {
  it('突出运行、暂停和结果不明确状态', () => {
    expect(statusColor('RUNNING')).toBe('green')
    expect(statusColor('ACTIVE')).toBe('green')
    expect(statusColor('PAUSED')).toBe('orange')
    expect(statusColor('OUTCOME_UNKNOWN')).toBe('orange')
    expect(statusColor('STOPPED')).toBe('default')
  })
})

describe('Worker 状态展示', () => {
  const workers = [
    { worker_id: 'worker-current', status: 'RUNNING' },
    { worker_id: 'worker-old', status: 'STOPPED' },
  ]

  it('总览只展示活动 Worker', () => {
    expect(activeWorkers(workers)).toEqual([workers[0]])
    expect(workerStatusText(workers)).toBe('worker-current:RUNNING')
  })

  it('没有活动 Worker 时显示未运行', () => {
    expect(workerStatusText([{ worker_id: 'worker-old', status: 'STOPPED' }])).toBe('未运行')
  })

  it('多个活动 Worker 时显示异常', () => {
    expect(workerStatusText([
      ...workers,
      { worker_id: 'worker-stale', status: 'STALE' },
    ])).toContain('异常：存在 2 个活动 Worker')
  })
})
