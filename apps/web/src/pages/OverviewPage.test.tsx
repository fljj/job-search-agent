import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { message } from 'antd'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../api/client'
import { OverviewPage } from './OverviewPage'
import { canReconnectRun, canResumeRun, processedJobAndMessageCount } from './run-summary'

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
    expect(canResumeRun({
      status: 'PAUSED',
      pause_reason_codes: ['USER_PAUSED'],
    })).toBe(true)
  })

  it('已处理职位和消息不计入脉脉推荐', () => {
    expect(processedJobAndMessageCount([
      pausedRun,
      { ...pausedRun, id: 'maimai-run', platform: 'MAIMAI', processed_count: 99 },
      { ...pausedRun, id: 'liepin-run', platform: 'LIEPIN', processed_count: 4 },
    ])).toBe(14)
  })

  it('分别展示人工确认和面试确认数量', async () => {
    render(<OverviewPage />)

    expect(await screen.findByText('待人工处理')).toBeTruthy()
    expect(screen.getByText('待确认面试')).toBeTruthy()
    expect(await screen.findByText('3')).toBeTruthy()
    expect(await screen.findByText('2')).toBeTruthy()
    expect(screen.getByText('LLM：正常可用 / 日历：已配置 / 执行器：已配置')).toBeTruthy()
    expect(screen.queryByText(/LLM:CLOSED/)).toBeNull()
  })

  it('人工暂停显示中文原因并允许恢复运行', async () => {
    vi.mocked(api).mockImplementation(async (path) => {
      if (path === '/automation/runs') return { items: [{
        ...pausedRun,
        id: 'liepin-run',
        platform: 'LIEPIN',
        pause_reason_codes: ['USER_PAUSED'],
      }] }
      if (path === '/automation/overview') return {
        job_count: 0,
        active_conversation_count: 0,
        successful_action_count: 0,
        waiting_message_count: 0,
        failed_action_count: 0,
      }
      if (path === '/automation/operations/status') return {
        database_ready: true,
        llm_configured: true,
        unknown_action_count: 0,
        pending_confirmation_count: 0,
        pending_human_confirmation_count: 0,
        pending_schedule_confirmation_count: 0,
        workers: [{ worker_id: 'worker-1', status: 'RUNNING' }],
        discrepancies: [],
        platform_readiness: [],
        capabilities: { llm: 'CLOSED', calendar: 'CONFIGURED', executor: 'CONFIGURED' },
        llm_circuit: { status: 'CLOSED', provider: 'ZHIPU', model: 'glm-5.2' },
      }
      if (path === '/automation/runs/liepin-run/resume') return {}
      return {}
    })
    const successSpy = vi.spyOn(message, 'success').mockImplementation(() => undefined as never)

    render(<OverviewPage />)

    expect(await screen.findByText('LIEPIN:已暂停（人工暂停）')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: '恢复运行' }))
    await waitFor(() => expect(api).toHaveBeenCalledWith(
      '/automation/runs/liepin-run/resume',
      { method: 'POST' },
    ))
    await waitFor(() => expect(successSpy).toHaveBeenCalledWith('LIEPIN 已恢复运行'))
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
