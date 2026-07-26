import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { message } from 'antd'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../api/client'
import { AutomationPage } from './AutomationPage'

vi.mock('../api/client', () => ({ api: vi.fn() }))

const bossRollout = {
  platform: 'BOSS',
  status: 'ACTIVE',
  current_level: 2,
  level_name: '自动回复',
  previous_level: 1,
  stage_started_at: '2026-07-26T08:00:00+08:00',
  minimum_stage_hours: 24,
  remaining_hours: 12,
  reply_daily_limit: 5,
  greeting_daily_limit: 3,
  safety_metrics: {},
  version: 7,
}

const operations = {
  database_ready: true,
  migration_revision: '20260726_0024',
  llm_configured: true,
  selector_version: 'v9',
  executor_mode: 'PLAYWRIGHT',
  calendar_provider: 'APPLE',
  unknown_action_count: 0,
  pending_confirmation_count: 0,
  workers: [
    { worker_id: 'boss-worker', status: 'RUNNING', heartbeat_at: '2026-07-26T08:00:00Z' },
    { worker_id: 'old-worker', status: 'STOPPED', heartbeat_at: '2026-07-25T08:00:00Z' },
  ],
  reconciliation_tasks: [],
  discrepancies: [],
}

function mockAutomationApi() {
  vi.mocked(api).mockImplementation(async (path) => {
    if (path === '/system/llm-status') return { provider: 'ZHIPU', model: 'glm-5.2', configured: true }
    if (path === '/automation/runs') return { items: [] }
    if (path === '/automation/actions') return { items: [] }
    if (path === '/automation/operations/status') return operations
    if (path === '/automation/rollouts') {
      return {
        items: [
          { ...bossRollout, platform: 'MAIMAI', current_level: 6, level_name: '脉脉全量' },
          bossRollout,
        ],
      }
    }
    if (path === '/strategies?enabled=true') return { items: [{ id: 'strategy-1', name: '主策略', enabled: true }] }
    return {}
  })
}

describe('AutomationPage', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    vi.clearAllMocks()
    mockAutomationApi()
  })

  it('只展示活动 Worker，并按 BOSS 灰度等待时间禁止升级', async () => {
    render(<AutomationPage />)

    await screen.findByText('boss-worker:RUNNING')
    expect(screen.queryByText(/old-worker/)).toBeNull()
    expect(screen.getByText('2 - 自动回复')).toBeTruthy()
    expect(screen.queryByText(/脉脉全量/)).toBeNull()
    expect(screen.getByRole('button', { name: '升级一级' })).toHaveProperty('disabled', true)
  })

  it('紧急停止通过配置接口提交，平台选择不改变 BOSS 灰度接口', async () => {
    render(<AutomationPage />)
    await screen.findByText('boss-worker:RUNNING')

    fireEvent.click(screen.getByRole('switch', { name: '紧急停止' }))
    fireEvent.click(screen.getByRole('button', { name: '保存配置' }))

    await waitFor(() => expect(api).toHaveBeenCalledWith(
      '/automation/settings',
      expect.objectContaining({
        method: 'PUT',
        body: expect.stringContaining('"emergency_stop":true'),
      }),
    ))
  })

  it('服务端拒绝灰度操作时展示错误，不绕过服务端权限', async () => {
    const errorSpy = vi.spyOn(message, 'error').mockImplementation(() => undefined as never)
    vi.mocked(api).mockImplementation(async (path) => {
      if (path === '/system/llm-status') return { provider: 'ZHIPU', model: 'glm-5.2', configured: true }
      if (path === '/automation/runs') return { items: [] }
      if (path === '/automation/actions') return { items: [] }
      if (path === '/automation/operations/status') {
        return { ...operations, unknown_action_count: 1 }
      }
      if (path === '/automation/rollouts') {
        return { items: [{ ...bossRollout, remaining_hours: 0 }] }
      }
      if (path === '/strategies?enabled=true') return { items: [] }
      if (path === '/automation/rollouts/BOSS/transition') throw new Error('灰度安全指标未通过')
      return {}
    })

    render(<AutomationPage />)
    const advance = await screen.findByRole('button', { name: '升级一级' })
    expect(advance).toHaveProperty('disabled', false)
    fireEvent.click(advance)

    await waitFor(() => expect(api).toHaveBeenCalledWith(
      '/automation/rollouts/BOSS/transition',
      {
        method: 'POST',
        body: JSON.stringify({ action: 'ADVANCE', expected_version: 7 }),
      },
    ))
    await waitFor(() => expect(errorSpy).toHaveBeenCalledWith('灰度安全指标未通过'))
  })
})
