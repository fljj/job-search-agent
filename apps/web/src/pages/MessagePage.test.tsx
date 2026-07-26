import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../api/client'
import { MessagePage } from './MessagePage'

vi.mock('../api/client', () => ({ api: vi.fn() }))

describe('MessagePage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('不匹配会话明确显示不会发送简历且不展示附件名', async () => {
    vi.mocked(api).mockResolvedValue({
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
        latest_draft_decision: 'DENY',
        latest_draft_reason_codes: ['RESUME_SEND_DENIED', 'JOB_DIRECTION_CONFLICT'],
      }],
    })

    render(<MessagePage />)

    await screen.findByText('不匹配')
    expect(screen.getAllByText('岗位方向不符合求职方向').length).toBeGreaterThan(0)
    expect(screen.getByText('不发送简历')).toBeTruthy()
    expect(screen.getByText('不会执行')).toBeTruthy()
    expect(screen.queryByText('默认简历.pdf')).toBeNull()
  })

})
