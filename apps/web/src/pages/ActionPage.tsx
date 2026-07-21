import { Alert, Button, Card, Input, Space, Table, Tag, message } from 'antd'
import { useEffect, useState } from 'react'
import { api } from '../api/client'

interface Task { id: string; status: string; action_type?: string; content?: string; reason_codes: string[];
  confidence?: number; platform?: string; company?: string; job_title?: string; recruiter?: string }

export function ActionPage() {
  const [tasks, setTasks] = useState<Task[]>([])
  const [conversationId, setConversationId] = useState('')
  const [resumeId, setResumeId] = useState('')
  const [editedContent, setEditedContent] = useState('')
  const [actionId, setActionId] = useState('')
  const [cdpUrl, setCdpUrl] = useState('http://127.0.0.1:9222')
  const load = () => api<{ items: Task[] }>('/confirmation-tasks').then((data) => setTasks(data.items))
  useEffect(() => { api<{ items: Task[] }>('/confirmation-tasks').then((data) => setTasks(data.items)) }, [])
  const approve = async (task: Task) => {
    if (!conversationId) return message.warning('请填写目标对话 ID')
    const action = await api<{ id: string }>(`/confirmation-tasks/${task.id}/approve`, {
      method: 'POST', headers: { 'Idempotency-Key': crypto.randomUUID() },
      body: JSON.stringify({ conversation_id: conversationId }),
    })
    setActionId(action.id); await load(); message.success('动作已批准，仍需单独执行')
  }
  const modify = async (task: Task) => {
    if (!editedContent) return message.warning('请填写修改内容')
    await api(`/confirmation-tasks/${task.id}/modify`, { method: 'POST',
      body: JSON.stringify({ content: editedContent }) }); await load()
  }
  const reject = async (task: Task) => { await api(`/confirmation-tasks/${task.id}/reject`, { method: 'POST' }); await load() }
  const createResumeTask = async () => {
    if (!conversationId || !resumeId) return message.warning('请填写目标对话 ID 和简历附件 ID')
    await api('/confirmation-tasks/resume', { method: 'POST',
      body: JSON.stringify({ conversation_id: conversationId, resume_id: resumeId }) })
    await load(); message.success('已创建简历发送确认任务')
  }
  const execute = async () => {
    if (!actionId) return message.warning('请先批准动作或填写动作 ID')
    const result = await api<{ status: string }>(`/actions/${actionId}/execute`, { method: 'POST',
      body: JSON.stringify({ cdp_url: cdpUrl }) }); message.info(`执行结果：${result.status}`); await load()
  }
  return <Space direction="vertical" style={{ width: '100%' }}>
    <Alert type="error" showIcon message="手动发送阶段" description="批准不会立即发送。执行前请手动打开正确对话页，系统会重新核对平台、公司、职位、招聘人、对话和附件。" />
    <Card title="确认上下文"><Space direction="vertical" style={{ width: '100%' }}>
      <Input value={conversationId} onChange={(event) => setConversationId(event.target.value)} placeholder="目标对话 ID" />
      <Space.Compact style={{ width: '100%' }}>
        <Input value={resumeId} onChange={(event) => setResumeId(event.target.value)} placeholder="网站内已有简历附件 ID" />
        <Button onClick={() => void createResumeTask()}>创建简历确认</Button>
      </Space.Compact>
      <Input.TextArea value={editedContent} onChange={(event) => setEditedContent(event.target.value)} placeholder="修改后内容（修改后必须再次确认）" />
    </Space></Card>
    <Card title="待确认队列"><Table rowKey="id" dataSource={tasks} columns={[
      { title: '类型', dataIndex: 'action_type' },
      { title: '平台', dataIndex: 'platform' }, { title: '公司', dataIndex: 'company' },
      { title: '职位', dataIndex: 'job_title' }, { title: '招聘人', dataIndex: 'recruiter' },
      { title: '状态', dataIndex: 'status', render: (value: string) => <Tag>{value}</Tag> },
      { title: '内容', dataIndex: 'content' },
      { title: '原因', dataIndex: 'reason_codes', render: (items: string[]) => items.join('、') },
      { title: '操作', render: (_: unknown, task: Task) => <Space>
        <Button disabled={task.status !== 'PENDING_APPROVAL'} onClick={() => void approve(task)}>批准</Button>
        <Button disabled={task.status !== 'PENDING_APPROVAL'} onClick={() => void modify(task)}>修改并重新检查</Button>
        <Button danger disabled={task.status !== 'PENDING_APPROVAL'} onClick={() => void reject(task)}>拒绝</Button>
      </Space> },
    ]} /></Card>
    <Card title="执行已批准动作"><Space direction="vertical" style={{ width: '100%' }}>
      <Input value={actionId} onChange={(event) => setActionId(event.target.value)} placeholder="已批准动作 ID" />
      <Input value={cdpUrl} onChange={(event) => setCdpUrl(event.target.value)} />
      <Button type="primary" danger onClick={() => void execute()}>复核当前页并执行发送</Button>
    </Space></Card>
  </Space>
}
