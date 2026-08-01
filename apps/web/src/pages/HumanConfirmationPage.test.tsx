import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../api/client'
import { HumanConfirmationPage } from './HumanConfirmationPage'

vi.mock('../api/client', () => ({ api: vi.fn() }))

describe('HumanConfirmationPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(api).mockImplementation(async (path) => {
      if (path === '/confirmation-tasks') return { items: [{
        id: 'task-1', status: 'PENDING_APPROVAL', action_type: 'REPLY',
        reason_codes: ['LLM_FAILURE_REQUIRES_HUMAN'], content: '请人工确认',
        platform: 'MAIMAI', recruiter: '张女士', conversation_id: 'conversation-1',
        expires_at: '2099-01-01T00:00:00Z',
      }] }
      if (path === '/confirmation-tasks/task-1/approve') return { id: 'action-1' }
      if (path === '/actions/action-1/execute') return { id: 'action-1', status: 'SUCCEEDED' }
      return {}
    })
  })

  it('展示普通人工任务并在明确确认后执行发送', async () => {
    render(<HumanConfirmationPage />)

    expect(await screen.findByText('AI 暂不可用，需要人工处理')).toBeTruthy()
    expect(screen.getByDisplayValue('请人工确认')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: '批准并发送' }))
    fireEvent.click(await screen.findByText('确 认'))

    await waitFor(() => expect(api).toHaveBeenCalledWith(
      '/confirmation-tasks/task-1/approve',
      expect.objectContaining({ method: 'POST' }),
    ))
    await waitFor(() => expect(api).toHaveBeenCalledWith(
      '/actions/action-1/execute',
      { method: 'POST', body: '{}' },
    ))
  })
})
