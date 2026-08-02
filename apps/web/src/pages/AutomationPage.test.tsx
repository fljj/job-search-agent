import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../api/client'
import { AutomationPage } from './AutomationPage'

vi.mock('../api/client', () => ({ api: vi.fn() }))

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
    if (path === '/system/llm-status') return {
      provider: 'ZHIPU', model: 'glm-5.2', timeout_seconds: 120, configured: true,
      options: [
        { provider: 'ZHIPU', model: 'glm-5.2', configured: true },
        { provider: 'QWEN', model: 'qwen-plus', configured: true },
      ],
    }
    if (path === '/automation/runs') return { items: [] }
    if (path === '/automation/actions') return { items: [] }
    if (path === '/automation/operations/status') return operations
    if (path === '/strategies?enabled=true') return { items: [{ id: 'strategy-1', name: '主策略', enabled: true }] }
    if (path === '/automation/settings') return { items: [
      {
        scope_type: 'GLOBAL', scope_key: 'GLOBAL', enabled: true, paused: false,
        auto_greet_enabled: true, auto_reply_enabled: true,
        auto_resume_enabled: true, maimai_recommendation_enabled: true,
        maimai_recommendation_resume_enabled: true, emergency_stop: true,
        job_scan_enabled: true, company_cooldown_hours: 24, recruiter_cooldown_hours: 24,
        work_start_hour: 8, work_end_hour: 22,
      },
      {
        scope_type: 'PLATFORM', scope_key: 'LIEPIN', enabled: true, paused: true,
        auto_greet_enabled: true, auto_reply_enabled: true,
        auto_resume_enabled: true, maimai_recommendation_enabled: true,
        maimai_recommendation_resume_enabled: true, emergency_stop: false,
        job_scan_enabled: true, company_cooldown_hours: 24, recruiter_cooldown_hours: 24,
        work_start_hour: 8, work_end_hour: 22,
      },
    ] }
    return {}
  })
}

describe('AutomationPage', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    vi.clearAllMocks()
    mockAutomationApi()
  })

  it('只展示活动 Worker，不再请求或展示灰度控制', async () => {
    render(<AutomationPage />)

    await screen.findByText('boss-worker:RUNNING')
    expect(screen.queryByText(/old-worker/)).toBeNull()
    expect(screen.queryByText(/无人值守灰度/)).toBeNull()
    expect(api).not.toHaveBeenCalledWith('/automation/rollouts')
  })

  it('紧急停止通过正式自动化配置接口提交', async () => {
    render(<AutomationPage />)
    await screen.findByText('boss-worker:RUNNING')

    fireEvent.click(screen.getByRole('switch', { name: '紧急停止' }))
    fireEvent.click(screen.getByRole('button', { name: '保存配置' }))

    await waitFor(() => expect(api).toHaveBeenCalledWith(
      '/automation/settings',
      expect.objectContaining({
        method: 'PUT',
        body: expect.stringContaining('"emergency_stop":false'),
      }),
    ))
  })

  it('平台配置通过平台名称选择，不需要填写内部范围标识', async () => {
    const user = userEvent.setup()
    render(<AutomationPage />)
    await screen.findByText('boss-worker:RUNNING')

    expect(screen.queryByText('范围标识')).toBeNull()
    await user.click(screen.getByLabelText('配置范围'))
    await user.click(await screen.findByTitle('指定平台'))
    await user.click(screen.getByLabelText('配置平台'))
    await user.click(await screen.findByTitle('猎聘'))
    expect(screen.getByLabelText('临时暂停').getAttribute('aria-checked')).toBe('true')

    await user.click(screen.getByLabelText('临时暂停'))
    await user.click(screen.getByRole('button', { name: '保存配置' }))

    await waitFor(() => expect(api).toHaveBeenCalledWith(
      '/automation/settings',
      expect.objectContaining({
        method: 'PUT',
        body: expect.stringContaining('"scope_type":"PLATFORM","scope_key":"LIEPIN"'),
      }),
    ))
  })

  it('供应商从环境允许列表选择，模型可以手动输入并热切换', async () => {
    const user = userEvent.setup()
    render(<AutomationPage />)
    await screen.findByText('boss-worker:RUNNING')

    await user.click(screen.getByLabelText('LLM 供应商'))
    await user.click(await screen.findByTitle('QWEN'))
    await user.clear(screen.getByLabelText('LLM 模型'))
    await user.type(screen.getByLabelText('LLM 模型'), 'qwen-max-latest')
    await user.clear(screen.getByLabelText('LLM 超时时间'))
    await user.type(screen.getByLabelText('LLM 超时时间'), '240')
    await user.click(screen.getByRole('button', { name: '应用 LLM 配置' }))

    await waitFor(() => expect(api).toHaveBeenCalledWith(
      '/system/llm-status',
      expect.objectContaining({
        method: 'PUT',
        body: JSON.stringify({
          provider: 'QWEN', model: 'qwen-max-latest', timeout_seconds: 240,
        }),
      }),
    ))
  })

  it('猎聘不展示阶段文案，已停止的平台可重新启动', async () => {
    vi.mocked(api).mockImplementation(async (path, options) => {
      if (path === '/automation/runs' && options?.method === 'POST') return {}
      if (path === '/automation/runs') return { items: [{
        id: 'liepin-run', platform: 'LIEPIN', strategy_id: 'strategy-1', status: 'STOPPED',
        processed_count: 0, action_count: 0, failure_count: 0,
        consecutive_failure_count: 0, pause_reason_codes: [], cursor: {},
      }] }
      if (path === '/system/llm-status') return {
        provider: 'ZHIPU', model: 'glm-5.2', timeout_seconds: 120, configured: true,
        options: [{ provider: 'ZHIPU', model: 'glm-5.2', configured: true }],
      }
      if (path === '/automation/actions') return { items: [] }
      if (path === '/automation/operations/status') return operations
      if (path === '/strategies?enabled=true') return { items: [{ id: 'strategy-1', name: '主策略', enabled: true }] }
      if (path === '/automation/settings') return { items: [] }
      return {}
    })

    render(<AutomationPage />)

    await screen.findByText('平台任务管理（高级）')
    expect(screen.queryByText(/L4 已就绪/)).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: '重新启动' }))

    await waitFor(() => expect(api).toHaveBeenCalledWith(
      '/automation/runs',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ platform: 'LIEPIN', strategy_id: 'strategy-1' }),
      }),
    ))
  })

  it('平台任务表只展示紧凑扫描进度，不渲染完整游标', async () => {
    vi.mocked(api).mockImplementation(async (path) => {
      if (path === '/automation/runs') return { items: [{
        id: 'boss-run', platform: 'BOSS', strategy_id: 'strategy-1', status: 'RUNNING',
        processed_count: 3, action_count: 1, failure_count: 0,
        consecutive_failure_count: 0, pause_reason_codes: [],
        cursor: {
          job_discovery: { search_key: 'Java', scroll_position: 12, seen_job_ids: ['不应展示的职位ID'] },
          message_discovery: { scroll_position: 8, seen_message_keys: ['不应展示的消息ID'] },
        },
      }] }
      if (path === '/system/llm-status') return {
        provider: 'ZHIPU', model: 'glm-5.2', timeout_seconds: 120, configured: true,
        options: [{ provider: 'ZHIPU', model: 'glm-5.2', configured: true }],
      }
      if (path === '/automation/actions') return { items: [] }
      if (path === '/automation/operations/status') return operations
      if (path === '/strategies?enabled=true') return { items: [{ id: 'strategy-1', name: '主策略', enabled: true }] }
      if (path === '/automation/settings') return { items: [] }
      return {}
    })

    render(<AutomationPage />)

    await screen.findByText('职位 Java · 位置 12；消息位置 8')
    expect(screen.queryByText(/不应展示的职位ID/)).toBeNull()
    expect(screen.queryByText(/不应展示的消息ID/)).toBeNull()
  })

})
