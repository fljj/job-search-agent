import { describe, expect, it } from 'vitest'
import { statusColor } from './pages/automation-status'

describe('Agent 控制台状态展示', () => {
  it('突出运行、暂停和结果不明确状态', () => {
    expect(statusColor('RUNNING')).toBe('green')
    expect(statusColor('ACTIVE')).toBe('green')
    expect(statusColor('PAUSED')).toBe('orange')
    expect(statusColor('OUTCOME_UNKNOWN')).toBe('orange')
    expect(statusColor('STOPPED')).toBe('default')
  })
})
