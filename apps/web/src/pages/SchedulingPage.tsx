import { Alert, Button, Card, Checkbox, Input, Space, Table, Tag, message } from 'antd'
import { useEffect, useState } from 'react'
import { api } from '../api/client'

interface ScheduleRequest {
  id: string; event_type: string; source_text: string; start_at?: string; end_at?: string
  timezone: string; status: string; calendar_status?: string; candidate_slots: Array<{ start_at: string; end_at: string }>
  suggested_reply?: string; create_calendar_event: boolean
}
interface CalendarStatus {
  provider: string; calendar_id: string; configured: boolean; real_provider: boolean
}

export function SchedulingPage() {
  const [items, setItems] = useState<ScheduleRequest[]>([])
  const [reply, setReply] = useState('')
  const [createEvent, setCreateEvent] = useState(false)
  const [calendar, setCalendar] = useState<CalendarStatus>()
  const load = () => api<{ items: ScheduleRequest[] }>('/scheduling/requests').then((data) => setItems(data.items))
  useEffect(() => {
    void load()
    void api<CalendarStatus>('/system/calendar-status').then(setCalendar)
  }, [])
  const approve = async (item: ScheduleRequest) => {
    const content = reply || item.suggested_reply
    if (!content) return message.warning('请填写确认后的回复内容')
    await api(`/scheduling/requests/${item.id}/approve`, { method: 'POST',
      body: JSON.stringify({ reply_content: content, create_calendar_event: createEvent }) })
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
  return <Space direction="vertical" style={{ width: '100%' }}>
    <Alert type="warning" showIcon message="日历空闲也必须人工确认"
      description="时间回复与日历写入是两个独立授权。发送前会检查日历快照；出现新冲突会退回待确认。" />
    <Alert type={calendar?.configured ? 'info' : 'error'} showIcon
      message={`日历：${calendar?.provider ?? '-'} / ${calendar?.configured ? '已配置' : '未配置'}`}
      description={calendar?.real_provider
        ? `真实日历 ${calendar.calendar_id}；读取忙闲与创建事件仍是独立操作。`
        : '当前使用本地模拟日历，不代表真实日历空闲。'} />
    <Card title="时间确认任务" extra={<Button onClick={() => void load()}>刷新</Button>}>
      <Space direction="vertical" style={{ width: '100%', marginBottom: 16 }}>
        <Input.TextArea value={reply} onChange={(event) => setReply(event.target.value)}
          placeholder="可选：填写修改后的回复；留空时使用任务建议回复" />
        <Checkbox checked={createEvent} onChange={(event) => setCreateEvent(event.target.checked)}>
          发送成功后授权创建日历事件</Checkbox>
      </Space>
      <Table rowKey="id" dataSource={items} columns={[
      { title: '类型', dataIndex: 'event_type' },
      { title: '原邀请', dataIndex: 'source_text' },
      { title: '解析时间', render: (_: unknown, item: ScheduleRequest) => item.start_at ? `${item.start_at} — ${item.end_at}` : '待澄清' },
      { title: '日历', dataIndex: 'calendar_status', render: (value: string) => <Tag>{value}</Tag> },
      { title: '状态', dataIndex: 'status', render: (value: string) => <Tag>{value}</Tag> },
      { title: '建议', dataIndex: 'suggested_reply' },
      { title: '操作', render: (_: unknown, item: ScheduleRequest) => <Space>
        <Button disabled={item.status !== 'PENDING_APPROVAL'} onClick={() => void approve(item)}>确认时间回复</Button>
        <Button disabled={!['PENDING_APPROVAL', 'APPROVED'].includes(item.status)}
          onClick={() => void reject(item)}>拒绝</Button>
        <Button danger disabled={item.status !== 'APPROVED'} onClick={() => void execute(item)}>复核并发送</Button>
      </Space> },
    ]} /></Card>
  </Space>
}
