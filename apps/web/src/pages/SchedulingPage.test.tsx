import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { message } from 'antd'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../api/client'
import { SchedulingPage } from './SchedulingPage'

vi.mock('../api/client', () => ({ api: vi.fn() }))

const requests = [
  {
    id: 'schedule-1',
    event_type: 'PHONE',
    source_text: '明天下午电话沟通',
    timezone: 'Asia/Shanghai',
    status: 'PENDING_APPROVAL',
    calendar_status: 'AVAILABLE',
    candidate_slots: [],
    suggested_reply: '第一条默认回复',
    create_calendar_event: false,
    qualification_evidence: [],
  },
  {
    id: 'schedule-2',
    event_type: 'INTERVIEW',
    source_text: '后天下午面试',
    timezone: 'Asia/Shanghai',
    status: 'PENDING_APPROVAL',
    calendar_status: 'AVAILABLE',
    candidate_slots: [],
    suggested_reply: '第二条默认回复',
    create_calendar_event: false,
    qualification_evidence: [],
  },
  {
    id: 'schedule-finished',
    event_type: 'PHONE',
    source_text: '已处理任务',
    timezone: 'Asia/Shanghai',
    status: 'EXECUTED',
    calendar_status: 'AVAILABLE',
    candidate_slots: [],
    create_calendar_event: false,
    qualification_evidence: [],
  },
]

function mockInitialRequests() {
  vi.mocked(api).mockImplementation(async (path) => {
    if (path === '/scheduling/requests') return { items: requests }
    if (path === '/system/calendar-status') {
      return { provider: 'APPLE', calendar_id: '工作', configured: true, real_provider: true }
    }
    return {}
  })
}

describe('SchedulingPage', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    vi.clearAllMocks()
    mockInitialRequests()
  })

  it('按任务隔离回复和日历授权，并禁用非待确认任务', async () => {
    render(<SchedulingPage />)
    await screen.findByText('明天下午电话沟通')

    const rows = screen.getAllByRole('row')
    const firstRow = rows.find((row) => within(row).queryByText('明天下午电话沟通'))!
    const secondRow = rows.find((row) => within(row).queryByText('后天下午面试'))!
    const finishedRow = rows.find((row) => within(row).queryByText('已处理任务'))!

    fireEvent.change(within(firstRow).getByPlaceholderText('第一条默认回复'), {
      target: { value: '第一条人工确认回复' },
    })
    fireEvent.click(within(firstRow).getByRole('checkbox'))
    fireEvent.click(within(firstRow).getByRole('button', { name: '确认时间回复' }))

    await waitFor(() => expect(api).toHaveBeenCalledWith(
      '/scheduling/requests/schedule-1/approve',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          reply_content: '第一条人工确认回复',
          create_calendar_event: true,
        }),
      }),
    ))

    fireEvent.change(within(secondRow).getByPlaceholderText('第二条默认回复'), {
      target: { value: '第二条人工确认回复' },
    })
    fireEvent.click(within(secondRow).getByRole('button', { name: '确认时间回复' }))

    await waitFor(() => expect(api).toHaveBeenCalledWith(
      '/scheduling/requests/schedule-2/approve',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          reply_content: '第二条人工确认回复',
          create_calendar_event: false,
        }),
      }),
    ))

    expect(within(finishedRow).getByRole('button', { name: '确认时间回复' }))
      .toHaveProperty('disabled', true)
    expect(within(finishedRow).getByRole('button', { name: /拒.*绝/ }))
      .toHaveProperty('disabled', true)
  })

  it('服务端拒绝操作时展示错误且不改变其他任务内容', async () => {
    const errorSpy = vi.spyOn(message, 'error').mockImplementation(() => undefined as never)
    vi.mocked(api).mockImplementation(async (path) => {
      if (path === '/scheduling/requests') return { items: requests }
      if (path === '/system/calendar-status') {
        return { provider: 'APPLE', calendar_id: '工作', configured: true, real_provider: true }
      }
      if (path === '/scheduling/requests/schedule-1/reject') throw new Error('任务状态已变化')
      return {}
    })

    render(<SchedulingPage />)
    await screen.findByText('明天下午电话沟通')
    const rows = screen.getAllByRole('row')
    const firstRow = rows.find((row) => within(row).queryByText('明天下午电话沟通'))!
    const secondRow = rows.find((row) => within(row).queryByText('后天下午面试'))!

    fireEvent.change(within(secondRow).getByPlaceholderText('第二条默认回复'), {
      target: { value: '第二条仍保留' },
    })
    fireEvent.click(within(firstRow).getByRole('button', { name: /拒.*绝/ }))

    await waitFor(() => expect(errorSpy).toHaveBeenCalledWith('任务状态已变化'))
    expect(within(secondRow).getByDisplayValue('第二条仍保留')).toBeTruthy()
  })

  it('冲突任务必须选择服务端候选时间且提交选中的时间段', async () => {
    const user = userEvent.setup()
    const warningSpy = vi.spyOn(message, 'warning').mockImplementation(() => undefined as never)
    const conflictRequest = {
      ...requests[0],
      id: 'schedule-conflict',
      calendar_status: 'CONFLICT',
      candidate_slots: [{
        start_at: '2026-07-27T14:00:00+08:00',
        end_at: '2026-07-27T14:30:00+08:00',
      }],
    }
    vi.mocked(api).mockImplementation(async (path) => {
      if (path === '/scheduling/requests') return { items: [conflictRequest] }
      if (path === '/system/calendar-status') {
        return { provider: 'APPLE', calendar_id: '工作', configured: true, real_provider: true }
      }
      return {}
    })

    render(<SchedulingPage />)
    const approve = await screen.findByRole('button', { name: '确认时间回复' })
    fireEvent.click(approve)
    expect(warningSpy).toHaveBeenCalledWith('请先选择一个服务端建议的候选时间')
    expect(api).not.toHaveBeenCalledWith(
      '/scheduling/requests/schedule-conflict/approve',
      expect.anything(),
    )

    await user.click(screen.getByRole('combobox'))
    const optionLabel = new Date(conflictRequest.candidate_slots[0].start_at).toLocaleString()
    await user.click(await screen.findByText(optionLabel))
    await user.click(screen.getByRole('button', { name: '确认时间回复' }))

    await waitFor(() => expect(api).toHaveBeenCalledWith(
      '/scheduling/requests/schedule-conflict/approve',
      expect.objectContaining({
        method: 'POST',
        body: expect.stringContaining('"selected_start_at":"2026-07-27T14:00:00+08:00"'),
      }),
    ))
  })
})
