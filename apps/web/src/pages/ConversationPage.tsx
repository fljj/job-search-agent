import { Button, Card, Input, Space, Table, Tag, message } from 'antd'
import { useEffect, useState } from 'react'
import { api } from '../api/client'

interface KnowledgeItem { id: string; category: string; key: string; fact: string; sensitivity: string; version: number }
interface Resume { id: string; attachment_name: string; platform: string; target_directions: string[] }

const knowledgeTemplate = JSON.stringify({ category: 'TECH_STACK', key: 'Java',
  fact: '拥有 8 年 Java 后端开发经验', source: '用户确认', allowed_for_auto_reply: true,
  sensitivity: 'NORMAL', verified_at: new Date().toISOString() }, null, 2)
const resumeTemplate = JSON.stringify({ platform: 'MOCK', attachment_name: 'Java后端简历.pdf',
  target_directions: ['Java后端'], is_available: true }, null, 2)

export function ConversationPage() {
  const [knowledgeJson, setKnowledgeJson] = useState(knowledgeTemplate)
  const [resumeJson, setResumeJson] = useState(resumeTemplate)
  const [knowledge, setKnowledge] = useState<KnowledgeItem[]>([])
  const [resumes, setResumes] = useState<Resume[]>([])
  const [conversationId, setConversationId] = useState('')
  const [conversationJson, setConversationJson] = useState(JSON.stringify({ job_id: '请填写职位 ID',
    external_conversation_id: 'mock-conversation-1', recruiter_name: '模拟招聘人', platform: 'MOCK' }, null, 2))
  const [messageJson, setMessageJson] = useState(JSON.stringify({ external_message_id: 'mock-message-1',
    content: '请介绍一下你的 Java 技术栈经验', received_at: new Date().toISOString() }, null, 2))
  const [draft, setDraft] = useState<Record<string, unknown>>()
  const load = async () => {
    const [facts, resumeList] = await Promise.all([
      api<{ items: KnowledgeItem[] }>('/knowledge-items'), api<{ items: Resume[] }>('/resumes'),
    ])
    setKnowledge(facts.items); setResumes(resumeList.items)
  }
  useEffect(() => { Promise.all([
    api<{ items: KnowledgeItem[] }>('/knowledge-items'), api<{ items: Resume[] }>('/resumes'),
  ]).then(([facts, resumeList]) => { setKnowledge(facts.items); setResumes(resumeList.items) }) }, [])
  const createKnowledge = async () => { await api('/knowledge-items', { method: 'POST', body: knowledgeJson }); await load(); message.success('知识项已保存') }
  const createResume = async () => { await api('/resumes', { method: 'POST', body: resumeJson }); await load(); message.success('简历元数据已保存') }
  const analyze = async () => {
    if (!conversationId) return message.warning('请填写模拟对话 ID')
    const imported = await api<{ id: string }>(`/conversations/${conversationId}/messages`, { method: 'POST', body: messageJson })
    setDraft(await api<Record<string, unknown>>('/drafts/reply', { method: 'POST', body: JSON.stringify({ message_id: imported.id }) }))
  }
  const createConversation = async () => {
    const created = await api<{ id: string }>('/conversations', { method: 'POST', body: conversationJson })
    setConversationId(created.id); message.success('模拟对话已创建')
  }
  return <Space direction="vertical" style={{ width: '100%' }}>
    <Card title="候选人知识库"><Input.TextArea rows={8} value={knowledgeJson} onChange={(event) => setKnowledgeJson(event.target.value)} />
      <Button type="primary" onClick={() => void createKnowledge()} style={{ marginTop: 12 }}>新增知识项</Button>
      <Table rowKey="id" pagination={false} dataSource={knowledge} columns={[{ title: '分类', dataIndex: 'category' },
        { title: '键', dataIndex: 'key' }, { title: '事实', dataIndex: 'fact' },
        { title: '敏感度', dataIndex: 'sensitivity', render: (value: string) => <Tag>{value}</Tag> }]} /></Card>
    <Card title="网站内附件简历元数据"><Input.TextArea rows={6} value={resumeJson} onChange={(event) => setResumeJson(event.target.value)} />
      <Button type="primary" onClick={() => void createResume()} style={{ marginTop: 12 }}>登记简历</Button>
      <Table rowKey="id" pagination={false} dataSource={resumes} columns={[{ title: '附件名', dataIndex: 'attachment_name' },
        { title: '平台', dataIndex: 'platform' }, { title: '适用方向', dataIndex: 'target_directions', render: (items: string[]) => items.join('、') }]} /></Card>
    <Card title="模拟消息分析与回复草稿"><Input.TextArea rows={5} value={conversationJson} onChange={(event) => setConversationJson(event.target.value)} />
      <Button onClick={() => void createConversation()} style={{ marginTop: 12 }}>创建模拟对话</Button>
      <Input placeholder="模拟对话 ID" value={conversationId} onChange={(event) => setConversationId(event.target.value)} style={{ marginTop: 12 }} />
      <Input.TextArea rows={6} value={messageJson} onChange={(event) => setMessageJson(event.target.value)} style={{ marginTop: 12 }} />
      <Button type="primary" onClick={() => void analyze()} style={{ marginTop: 12 }}>导入并生成草稿</Button>
      {draft && <pre style={{ whiteSpace: 'pre-wrap' }}>{JSON.stringify(draft, null, 2)}</pre>}</Card>
  </Space>
}
