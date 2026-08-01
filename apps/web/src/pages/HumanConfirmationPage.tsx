import { Button, Card, Input, Popconfirm, Space, Table, Tag, message } from 'antd'
import { useCallback, useEffect, useState } from 'react'
import { api } from '../api/client'

interface ConfirmationTask {
  id: string
  status: string
  action_type: string
  reason_codes: string[]
  content?: string
  confidence?: number
  expires_at?: string
  platform?: string
  company?: string
  job_title?: string
  recruiter?: string
  conversation_id?: string
}

const reasonLabels: Record<string, string> = {
  LLM_FAILURE_REQUIRES_HUMAN: 'AI 暂不可用，需要人工处理',
  LOW_CONFIDENCE: '回复置信度不足',
  SENSITIVE: '涉及敏感信息',
  USER_EDIT_RECHECKED: '人工修改后重新确认',
}

const actionLabels: Record<string, string> = {
  REPLY: '发送回复',
  GREETING: '主动招呼',
  RESUME: '发送简历',
}

const statusLabels: Record<string, string> = {
  PENDING_APPROVAL: '待确认',
  APPROVED: '已批准',
  REJECTED: '已拒绝',
  SUPERSEDED: '已替换',
  EXPIRED: '已过期',
}

export function HumanConfirmationPage() {
  const [items, setItems] = useState<ConfirmationTask[]>([])
  const [contents, setContents] = useState<Record<string, string>>({})
  const [loadingId, setLoadingId] = useState<string>()
  const showError = (error: unknown) =>
    message.error(error instanceof Error ? error.message : '操作失败，请稍后重试')
  const load = useCallback(async () => {
    const data = await api<{ items: ConfirmationTask[] }>('/confirmation-tasks')
    setItems(data.items)
    setContents(Object.fromEntries(data.items.map((item) => [item.id, item.content ?? ''])))
  }, [])
  useEffect(() => {
    let active = true
    void api<{ items: ConfirmationTask[] }>('/confirmation-tasks').then((data) => {
      if (!active) return
      setItems(data.items)
      setContents(Object.fromEntries(data.items.map((item) => [item.id, item.content ?? ''])))
    }).catch(showError)
    return () => { active = false }
  }, [])

  const approveAndExecute = async (item: ConfirmationTask) => {
    setLoadingId(item.id)
    try {
      const action = await api<{ id: string }>(`/confirmation-tasks/${item.id}/approve`, {
        method: 'POST',
        headers: { 'Idempotency-Key': `confirmation:${item.id}` },
        body: JSON.stringify({ conversation_id: item.conversation_id ?? null }),
      })
      await api(`/actions/${action.id}/execute`, { method: 'POST', body: '{}' })
      message.success('已批准并执行发送')
      await load()
    } catch (error) {
      showError(error)
    } finally {
      setLoadingId(undefined)
    }
  }
  const modify = async (item: ConfirmationTask) => {
    const content = contents[item.id]?.trim()
    if (!content) return message.warning('回复内容不能为空')
    setLoadingId(item.id)
    try {
      await api(`/confirmation-tasks/${item.id}/modify`, {
        method: 'POST', body: JSON.stringify({ content }),
      })
      message.success('修改已保存，请确认新任务')
      await load()
    } catch (error) {
      showError(error)
    } finally {
      setLoadingId(undefined)
    }
  }
  const reject = async (item: ConfirmationTask) => {
    setLoadingId(item.id)
    try {
      await api(`/confirmation-tasks/${item.id}/reject`, { method: 'POST' })
      message.success('已拒绝该任务')
      await load()
    } catch (error) {
      showError(error)
    } finally {
      setLoadingId(undefined)
    }
  }

  return <Card title="普通人工确认任务"
    extra={<Button onClick={() => void load().catch(showError)}>刷新</Button>}>
    <Table rowKey="id" dataSource={items} pagination={{ pageSize: 20 }} columns={[
      { title: '目标', render: (_: unknown, item: ConfirmationTask) =>
        `${item.platform ?? '-'} / ${item.company ?? '-'} / ${item.job_title ?? '-'} / ${item.recruiter ?? '-'}` },
      { title: '动作', render: (_: unknown, item: ConfirmationTask) =>
        actionLabels[item.action_type] ?? item.action_type },
      { title: '原因', render: (_: unknown, item: ConfirmationTask) =>
        item.reason_codes.map((code) => reasonLabels[code] ?? code).join('、') || '-' },
      { title: '回复内容', render: (_: unknown, item: ConfirmationTask) =>
        <Input.TextArea value={contents[item.id] ?? ''} disabled={item.status !== 'PENDING_APPROVAL'}
          onChange={(event) => setContents((current) => ({
            ...current, [item.id]: event.target.value,
          }))} /> },
      { title: '状态', render: (_: unknown, item: ConfirmationTask) =>
        <Space direction="vertical" size={0}><Tag>{statusLabels[item.status] ?? item.status}</Tag>
          <span>{item.expires_at ? new Date(item.expires_at).toLocaleString() : '-'}</span></Space> },
      { title: '操作', render: (_: unknown, item: ConfirmationTask) => {
        const pending = item.status === 'PENDING_APPROVAL'
        return <Space>
          <Button disabled={!pending} loading={loadingId === item.id}
            onClick={() => void modify(item)}>保存修改</Button>
          <Popconfirm title="确认发送这条回复？" okText="确认" cancelText="取消" disabled={!pending}
            onConfirm={() => void approveAndExecute(item)}>
            <Button type="primary" disabled={!pending} loading={loadingId === item.id}>批准并发送</Button>
          </Popconfirm>
          <Button danger disabled={!pending} loading={loadingId === item.id}
            onClick={() => void reject(item)}>拒绝</Button>
        </Space>
      } },
    ]} />
  </Card>
}
