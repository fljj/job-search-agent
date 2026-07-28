import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../api/client'
import { JobPage } from './JobPage'

vi.mock('../api/client', () => ({ api: vi.fn() }))

describe('JobPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.location.hash = 'jobs'
  })

  it('显示主动招呼失败原因并支持跳转已有会话', async () => {
    vi.mocked(api)
      .mockResolvedValueOnce({
        items: [{ id: 'strategy-1', name: '远程后端', enabled: true }],
      })
      .mockResolvedValueOnce({
        items: [{
          id: 'job-1',
          title: 'Java后端',
          company_name: '示例公司',
          work_mode: 'REMOTE',
          source: 'BOSS',
          latest_score: {
            id: 'score-1',
            total_score: 97,
            grade: 'A',
            eligibility: 'ELIGIBLE',
            hard_rejected: false,
            effective_job_status: 'OPEN',
          },
          communication: {
            status: 'GREETING_RETRY_PENDING',
            failure_code: 'APPROVED_TARGET_PAGE_NOT_FOUND',
            reason_codes: ['PREWRITE_GREETING_RETRY'],
          },
        }],
      })

    render(<JobPage />)

    await screen.findByText('发送失败，等待重试')
    expect(screen.getByText('发送时没有找到对应职位页')).toBeTruthy()
    expect(screen.queryByText('查看对应消息')).toBeNull()
  })

  it('已有会话时可以跳转消息中心', async () => {
    vi.mocked(api)
      .mockResolvedValueOnce({
        items: [{ id: 'strategy-1', name: '远程后端', enabled: true }],
      })
      .mockResolvedValueOnce({
        items: [{
          id: 'job-1',
          title: 'Java后端',
          company_name: '示例公司',
          work_mode: 'REMOTE',
          source: 'BOSS',
          latest_score: {
            id: 'score-1',
            total_score: 97,
            grade: 'A',
            eligibility: 'ELIGIBLE',
            hard_rejected: false,
            effective_job_status: 'OPEN',
          },
          communication: {
            status: 'CONVERSATION_ACTIVE',
            conversation_id: 'conversation-1',
            reason_codes: [],
          },
        }],
      })

    render(<JobPage />)

    fireEvent.click(await screen.findByText('查看对应消息'))
    expect(window.location.hash).toBe('#messages?job_id=job-1')
  })
})
