import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../api/client'
import { MessagePage } from './MessagePage'

vi.mock('../api/client', () => ({ api: vi.fn() }))

describe('MessagePage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.location.hash = 'messages'
  })

  it('不匹配会话明确显示不会发送简历且不展示附件名', async () => {
    vi.mocked(api).mockResolvedValue({
      total: 1,
      items: [{
        id: 'conversation-1',
        platform: 'BOSS',
        recruiter_name: '招聘人',
        state: 'ACTIVE',
        company_name: '示例公司',
        job_title: '销售',
        qualification_status: 'MISMATCH',
        qualification_evidence: ['JOB_DIRECTION_CONFLICT'],
        latest_draft_type: 'RESUME',
        latest_draft_content: '默认简历.pdf',
        latest_reply_source: 'RULE_TEMPLATE',
        latest_draft_decision: 'DENY',
        latest_draft_reason_codes: ['RESUME_SEND_DENIED', 'JOB_DIRECTION_CONFLICT'],
      }],
    })

    render(<MessagePage />)

    await screen.findByText('不匹配')
    expect(screen.getAllByText('岗位方向不符合求职方向').length).toBeGreaterThan(0)
    expect(screen.getByText('不发送简历')).toBeTruthy()
    expect(screen.getByText('不会执行')).toBeTruthy()
    expect(screen.getByText('规则回复')).toBeTruthy()
    expect(screen.queryByText('默认简历.pdf')).toBeNull()
    expect(screen.queryByText('猎聘写入需完成 L5 授权')).toBeNull()
  })

  it('可以从消息跳转到对应职位', async () => {
    vi.mocked(api).mockResolvedValue({
      total: 1,
      items: [{
        id: 'conversation-1',
        platform: 'BOSS',
        recruiter_name: '招聘人',
        state: 'ACTIVE',
        company_name: '示例公司',
        job_id: 'job-1',
        job_title: 'Java后端',
        qualification_status: 'FULL_MATCH',
        qualification_evidence: [],
      }],
    })

    render(<MessagePage />)

    fireEvent.click(await screen.findByText('查看职位'))
    expect(window.location.hash).toBe('#jobs?job_id=job-1')
  })

  it('使用服务端总数分页并支持按平台筛选', async () => {
    vi.mocked(api)
      .mockResolvedValueOnce({
        total: 41,
        items: [{
          id: 'conversation-1',
          platform: 'BOSS',
          recruiter_name: '招聘人',
          state: 'ACTIVE',
          qualification_status: 'UNKNOWN',
          qualification_evidence: [],
        }],
      })
      .mockResolvedValueOnce({
        total: 1,
        items: [{
          id: 'conversation-21',
          platform: 'MAIMAI',
          recruiter_name: '脉脉招聘人',
          state: 'ACTIVE',
          qualification_status: 'UNKNOWN',
          qualification_evidence: [],
        }],
      })

    render(<MessagePage />)

    await screen.findByText('共 41 条会话')
    fireEvent.mouseDown(screen.getByText('全部平台'))
    fireEvent.click(await screen.findByText('脉脉'))

    await screen.findByText('脉脉招聘人')
    expect(api).toHaveBeenLastCalledWith(
      '/conversations?page=1&page_size=20&platform=MAIMAI',
    )
  })
})
