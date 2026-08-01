import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { message } from 'antd'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../api/client'
import { OverviewPage } from './OverviewPage'
import { canReconnectRun } from './run-summary'

vi.mock('../api/client', () => ({ api: vi.fn() }))

const pausedRun = {
  id: 'boss-run',
  platform: 'BOSS',
  status: 'PAUSED',
  processed_count: 10,
  action_count: 2,
  failure_count: 0,
  pause_reason_codes: ['MESSAGE_DISCOVERY_UNAVAILABLE'],
}

function mockOverviewApi() {
  vi.mocked(api).mockImplementation(async (path) => {
    if (path === '/automation/runs') return { items: [pausedRun] }
    if (path === '/automation/actions') return { items: [] }
    if (path === '/conversations') return { items: [] }
    if (path === '/automation/operations/status') {
      return {
        database_ready: true,
        llm_configured: true,
        unknown_action_count: 0,
        pending_confirmation_count: 0,
        pending_human_confirmation_count: 3,
        pending_schedule_confirmation_count: 2,
        workers: [{ worker_id: 'worker-1', status: 'RUNNING' }],
        discrepancies: [],
        capabilities: { llm: 'CLOSED', calendar: 'CONFIGURED', executor: 'CONFIGURED' },
        llm_circuit: {
          status: 'CLOSED',
          provider: 'ZHIPU',
          model: 'glm-5.2',
          probe_attempt_count: 0,
        },
      }
    }
    if (path === '/automation/runs/boss-run/resume') return pausedRun
    return {}
  })
}

describe('OverviewPage 重新连接', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    vi.clearAllMocks()
    mockOverviewApi()
  })

  it('允许页面不可用或结果已对账的暂停状态快捷重连', () => {
    expect(canReconnectRun(pausedRun)).toBe(true)
    expect(canReconnectRun({
      status: 'PAUSED',
      pause_reason_codes: ['RESULT_NOT_OBSERVED'],
    })).toBe(true)
    expect(canReconnectRun({
      status: 'PAUSED',
      pause_reason_codes: ['USER_PAUSED'],
    })).toBe(false)
  })

  it('分别展示人工确认和面试确认数量', async () => {
    render(<OverviewPage />)

    expect(await screen.findByText('待人工处理')).toBeTruthy()
    expect(screen.getByText('待确认面试')).toBeTruthy()
    expect(screen.getByText('3')).toBeTruthy()
    expect(screen.getByText('2')).toBeTruthy()
    expect(screen.getByText('LLM：正常可用 / 日历：已配置 / 执行器：已配置')).toBeTruthy()
    expect(screen.queryByText(/LLM:CLOSED/)).toBeNull()
  })

  it('点击后调用恢复接口并提示重新检查页面', async () => {
    const successSpy = vi.spyOn(message, 'success').mockImplementation(() => undefined as never)
    render(<OverviewPage />)

    fireEvent.click(await screen.findByRole('button', { name: '重新连接' }))

    await waitFor(() => expect(api).toHaveBeenCalledWith(
      '/automation/runs/boss-run/resume',
      { method: 'POST' },
    ))
    await waitFor(() => expect(successSpy).toHaveBeenCalledWith('BOSS 已恢复，正在重新检查页面'))
  })

  it('API 不可用时清空旧状态并显示服务不可用', async () => {
    vi.mocked(api).mockRejectedValue(new Error('连接失败'))

    render(<OverviewPage />)

    expect(await screen.findAllByText('服务不可用')).not.toHaveLength(0)
    expect(screen.queryByText('BOSS:RUNNING')).toBeNull()
  })

  it('LLM 熔断时展示提示并允许立即重试', async () => {
    vi.mocked(api).mockImplementation(async (path) => {
      if (path === '/automation/operations/status') return {
        database_ready: true,
        llm_configured: true,
        unknown_action_count: 0,
        pending_confirmation_count: 0,
        pending_human_confirmation_count: 0,
        pending_schedule_confirmation_count: 0,
        workers: [],
        discrepancies: [],
        llm_circuit: {
          status: 'OPEN',
          provider: 'ZHIPU',
          model: 'glm-5.2',
          failure_code: 'LLM_RATE_LIMITED',
          probe_attempt_count: 2,
          next_probe_at: '2026-07-28T10:00:00+08:00',
        },
      }
      if (path === '/automation/llm-circuit/retry') return {
        status: 'CLOSED',
        provider: 'ZHIPU',
        model: 'glm-5.2',
        probe_attempt_count: 0,
      }
      if (path === '/automation/runs') return { items: [] }
      if (path === '/automation/actions') return { items: [] }
      if (path === '/conversations') return { items: [] }
      return {}
    })
    const successSpy = vi.spyOn(message, 'success').mockImplementation(() => undefined as never)

    render(<OverviewPage />)
    fireEvent.click(await screen.findByRole('button', {
      name: '重新加载配置并重试 LLM',
    }))

    await waitFor(() => expect(api).toHaveBeenCalledWith(
      '/automation/llm-circuit/retry',
      { method: 'POST' },
    ))
    await waitFor(() => expect(successSpy).toHaveBeenCalledWith(
      'LLM 已恢复，Agent 将自动继续工作',
    ))
  })
})
