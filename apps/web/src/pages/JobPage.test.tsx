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
        total: 1,
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
        total: 1,
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

  it('有来源链接时可以打开原职位', async () => {
    vi.mocked(api)
      .mockResolvedValueOnce({
        items: [{ id: 'strategy-1', name: '远程后端', enabled: true }],
      })
      .mockResolvedValueOnce({
        total: 1,
        items: [{
          id: 'job-1', title: 'Java后端', company_name: '示例公司',
          work_mode: 'REMOTE', source: 'BOSS',
          source_url: 'https://www.zhipin.com/job_detail/job-1.html',
        }],
      })

    render(<JobPage />)

    const link = await screen.findByRole('link', { name: '打开原职位' })
    expect(link.getAttribute('href')).toBe(
      'https://www.zhipin.com/job_detail/job-1.html',
    )
    expect(link.getAttribute('target')).toBe('_blank')
  })

  it('消息关联职位未评分时不被策略筛选隐藏', async () => {
    window.location.hash = 'jobs?job_id=job-unscored'
    vi.mocked(api)
      .mockResolvedValueOnce({
        items: [{ id: 'strategy-1', name: '远程后端', enabled: true }],
      })
      .mockResolvedValueOnce({ items: [], total: 0 })
      .mockResolvedValueOnce({
        total: 1,
        items: [{
          id: 'job-unscored',
          title: 'AI应用开发工程师（JAVA）',
          company_name: '山东泽凯控股',
          work_mode: 'ONSITE',
          source: 'BOSS',
          communication: { status: 'CONVERSATION_ACTIVE', reason_codes: [] },
        }],
      })

    render(<JobPage />)

    await screen.findByText('AI应用开发工程师（JAVA）')
    expect(screen.getByText('正在查看消息关联的职位')).toBeTruthy()
    expect(api).toHaveBeenNthCalledWith(
      3,
      '/jobs?job_id=job-unscored',
    )
  })

  it('使用服务端总数并在翻页时请求下一页', async () => {
    vi.mocked(api)
      .mockResolvedValueOnce({
        items: [{ id: 'strategy-1', name: '远程后端', enabled: true }],
      })
      .mockResolvedValueOnce({
        total: 147,
        items: [{
          id: 'job-1',
          title: '第一页职位',
          company_name: '示例公司',
          work_mode: 'REMOTE',
          source: 'BOSS',
        }],
      })
      .mockResolvedValueOnce({
        total: 147,
        items: [{
          id: 'job-21',
          title: '第二页职位',
          company_name: '另一家公司',
          work_mode: 'REMOTE',
          source: 'BOSS',
        }],
      })

    render(<JobPage />)

    await screen.findByText('共 147 个职位')
    fireEvent.click(screen.getByTitle('2'))

    await screen.findByText('第二页职位')
    expect(api).toHaveBeenLastCalledWith(
      '/jobs?page=2&page_size=20&strategy_id=strategy-1',
    )
  })
})
