import { Alert, Button, Card, Checkbox, Input, Select, Space, Table, Tag, message } from 'antd'
import { useEffect, useRef, useState } from 'react'
import { api } from '../api/client'

interface ScheduleRequest {
  id: string; event_type: string; source_text: string; start_at?: string; end_at?: string
  timezone: string; status: string; calendar_status?: string; candidate_slots: Array<{ start_at: string; end_at: string }>
  suggested_reply?: string; create_calendar_event: boolean
  platform?: string; company_name?: string; job_title?: string; recruiter_name?: string
  qualification_status?: string; qualification_evidence: string[]
}
interface CalendarStatus {
  provider: string; calendar_id: string; configured: boolean; real_provider: boolean
}

export function SchedulingPage() {
  const [items, setItems] = useState<ScheduleRequest[]>([])
  const replies = useRef<Record<string, string>>({})
  const createEvents = useRef<Record<string, boolean>>({})
  const [selectedSlots, setSelectedSlots] = useState<Record<string, number>>({})
  const [calendar, setCalendar] = useState<CalendarStatus>()
  const showRequestError = (error: unknown) =>
    message.error(error instanceof Error ? error.message : '操作失败，请稍后重试')
  const load = () => api<{ items: ScheduleRequest[] }>('/scheduling/requests').then((data) => setItems(data.items))
  useEffect(() => {
    void load().catch(showRequestError)
    void api<CalendarStatus>('/system/calendar-status').then(setCalendar).catch(showRequestError)
  }, [])
  const approve = async (item: ScheduleRequest) => {
    const selectedIndex = selectedSlots[item.id]
    const selected = selectedIndex === undefined ? undefined : item.candidate_slots[selectedIndex]
    if (item.calendar_status !== 'AVAILABLE' && item.candidate_slots.length && !selected) {
      return message.warning('请先选择一个服务端建议的候选时间')
    }
    const content = replies.current[item.id] || (selected
      ? `原时间不便，${new Date(selected.start_at).toLocaleString()} 是否可以？`
      : item.suggested_reply)
    if (!content) return message.warning('请填写确认后的回复内容')
    await api(`/scheduling/requests/${item.id}/approve`, { method: 'POST',
      body: JSON.stringify({ reply_content: content,
        selected_start_at: selected?.start_at, selected_end_at: selected?.end_at,
        create_calendar_event: selected ? false : Boolean(createEvents.current[item.id]) }) })
    await load(); message.success('具体时间已批准，仍需单独执行发送')
  }
  const execute = async (item: ScheduleRequest) => {
    await api(`/scheduling/requests/${item.id}/execute`, { method: 'POST', body: '{}' })
    await load()
  }
  const reject = async (item: ScheduleRequest) => {
    await api(`/scheduling/requests/${item.id}/reject`, { method: 'POST' })
    await load(); message.success('已拒绝该时间安排')
  }
  return <Space orientation="vertical" style={{ width: '100%' }}>
    <Alert type="warning" showIcon title="日历空闲也必须人工确认"
      description="时间回复与日历写入是两个独立授权。发送前会检查日历快照；出现新冲突会退回待确认。" />
    <Alert type={calendar?.configured ? 'info' : 'error'} showIcon
      title={`日历：${calendar?.provider ?? '-'} / ${calendar?.configured ? '已配置' : '未配置'}`}
      description={calendar?.real_provider
        ? `真实日历 ${calendar.calendar_id}；读取忙闲与创建事件仍是独立操作。`
        : '当前使用本地模拟日历，不代表真实日历空闲。'} />
    <Card title="时间确认任务"
      extra={<Button onClick={() => void load().catch(showRequestError)}>刷新</Button>}>
      <Table rowKey="id" dataSource={items} columns={[
      { title: '目标', render: (_: unknown, item: ScheduleRequest) =>
        `${item.platform ?? '-'} / ${item.company_name ?? '-'} / ${item.job_title ?? '-'} / ${item.recruiter_name ?? '-'}` },
      { title: '资格', render: (_: unknown, item: ScheduleRequest) =>
        <Space orientation="vertical" size={0}><Tag>{item.qualification_status ?? 'UNKNOWN'}</Tag>
          <span>{item.qualification_evidence?.join('、') || '-'}</span></Space> },
      { title: '类型', dataIndex: 'event_type' },
      { title: '原邀请', dataIndex: 'source_text' },
      { title: '解析时间', render: (_: unknown, item: ScheduleRequest) => item.start_at ? `${item.start_at} — ${item.end_at}` : '待澄清' },
      { title: '日历', dataIndex: 'calendar_status', render: (value: string) => <Tag>{value}</Tag> },
      { title: '候选时间', render: (_: unknown, item: ScheduleRequest) => item.candidate_slots.length
        ? <Select style={{ width: 220 }} value={selectedSlots[item.id]}
            placeholder="选择候选时间" onChange={(value) =>
              setSelectedSlots((current) => ({ ...current, [item.id]: value }))}
            options={item.candidate_slots.map((slot, index) => ({
              value: index, label: new Date(slot.start_at).toLocaleString(),
            }))} />
        : '-' },
      { title: '状态', dataIndex: 'status', render: (value: string) => <Tag>{value}</Tag> },
      { title: '回复与日历', render: (_: unknown, item: ScheduleRequest) =>
        <Space orientation="vertical">
          <Input.TextArea defaultValue=""
            placeholder={item.suggested_reply || '填写确认后的回复'}
            onChange={(event) => { replies.current[item.id] = event.target.value }} />
          <Checkbox defaultChecked={false}
            onChange={(event) => { createEvents.current[item.id] = event.target.checked }}>
            发送成功后创建日历事件
          </Checkbox>
        </Space> },
      { title: '操作', render: (_: unknown, item: ScheduleRequest) => <Space>
        <Button disabled={item.status !== 'PENDING_APPROVAL'}
          onClick={() => void approve(item).catch(showRequestError)}>确认时间回复</Button>
        <Button disabled={!['PENDING_APPROVAL', 'APPROVED'].includes(item.status)}
          onClick={() => void reject(item).catch(showRequestError)}>拒绝</Button>
        <Button danger disabled={item.status !== 'APPROVED'}
          onClick={() => void execute(item).catch(showRequestError)}>复核并发送</Button>
      </Space> },
    ]} /></Card>
  </Space>
}
