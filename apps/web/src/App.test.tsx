import { describe, expect, it } from 'vitest'
import { statusColor } from './pages/automation-status'
import {
  activeRuns,
  agentStatusText,
  runStatusText,
  type RunSummaryItem,
} from './pages/run-summary'
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

  it('失联 Worker 不计入活动数量', () => {
    expect(workerStatusText([
      ...workers,
      { worker_id: 'worker-stale', status: 'STALE' },
    ])).toBe('worker-current:RUNNING')
  })

  it('多个运行中的 Worker 才显示异常', () => {
    expect(workerStatusText([
      ...workers,
      { worker_id: 'worker-another', status: 'RUNNING' },
    ])).toContain('异常：存在 2 个活动 Worker')
  })
})

describe('总览平台运行聚合', () => {
  const run = (
    platform: string,
    status: string,
    processedCount: number,
  ): RunSummaryItem => ({
    id: platform,
    platform,
    status,
    processed_count: processedCount,
    pause_reason_codes: [],
  })

  it('同时展示并汇总所有活动平台', () => {
    const runs = [
      run('MAIMAI', 'RUNNING', 3),
      run('BOSS', 'RUNNING', 5),
      run('MOCK', 'STOPPED', 100),
    ]

    expect(activeRuns(runs).map((item) => item.platform)).toEqual(['BOSS', 'MAIMAI'])
    expect(activeRuns(runs).reduce((sum, item) => sum + item.processed_count, 0)).toBe(8)
    expect(agentStatusText(runs)).toBe('2 个平台运行中')
  })

  it('部分平台暂停时显示部分运行', () => {
    expect(agentStatusText([
      run('BOSS', 'RUNNING', 0),
      run('MAIMAI', 'PAUSED', 0),
    ])).toBe('部分运行')
  })

  it('没有活动平台时显示未启动', () => {
    expect(agentStatusText([run('BOSS', 'STOPPED', 0)])).toBe('未启动')
  })

  it('平台已启用但没有 Worker 时不显示运行中', () => {
    const runs = [
      run('BOSS', 'RUNNING', 0),
      run('MAIMAI', 'RUNNING', 0),
    ]

    expect(agentStatusText(runs, false)).toBe('Worker 未运行（2 个平台已启用）')
    expect(runStatusText(runs[0], false)).toBe('已启用，等待 Worker')
  })
})
