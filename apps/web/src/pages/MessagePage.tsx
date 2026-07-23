import { Card, Space, Table, Tag } from 'antd'
import { useEffect, useState } from 'react'
import { api } from '../api/client'
import { statusColor } from './automation-status'

interface ConversationSummary {
  id: string; platform: string; recruiter_name: string; state: string; company_name?: string
  job_title?: string; latest_score?: number; latest_grade?: string
  qualification_status: 'UNKNOWN' | 'ROUGH_MATCH' | 'FULL_MATCH' | 'MISMATCH'
  qualification_evidence: string[]
  latest_draft_type?: string; latest_draft_content?: string
  resume_action_status?: string; resume_attachment_name?: string
}

export function MessagePage() {
  const [items, setItems] = useState<ConversationSummary[]>([])
  useEffect(() => {
    void api<{ items: ConversationSummary[] }>('/conversations').then((data) => setItems(data.items))
  }, [])
  return <Card title="招聘沟通监控" extra={<Tag color="blue">普通沟通由 Agent 自动处理</Tag>}>
    <Table rowKey="id" dataSource={items} columns={[
      { title: '平台', dataIndex: 'platform' },
      { title: '公司/职位', render: (_: unknown, item: ConversationSummary) =>
        <Space direction="vertical" size={0}><strong>{item.company_name ?? '-'}</strong>
          <span>{item.job_title ?? '-'}</span></Space> },
      { title: '招聘人', dataIndex: 'recruiter_name' },
      { title: '评分', render: (_: unknown, item: ConversationSummary) =>
        item.latest_score === undefined || item.latest_score === null
          ? <Tag>待评分</Tag> : <Tag color={item.latest_score >= 80 ? 'green' : 'blue'}>
            {item.latest_score} / {item.latest_grade}</Tag> },
      { title: '入站资格', render: (_: unknown, item: ConversationSummary) =>
        <Space direction="vertical" size={0}>
          <Tag color={item.qualification_status === 'FULL_MATCH' ? 'green'
            : item.qualification_status === 'ROUGH_MATCH' ? 'blue'
              : item.qualification_status === 'MISMATCH' ? 'red' : 'default'}>
            {item.qualification_status}
          </Tag>
          <span>{item.qualification_evidence.join(', ') || '-'}</span>
        </Space> },
      { title: '会话状态', dataIndex: 'state',
        render: (value: string) => <Tag color={statusColor(value)}>{value}</Tag> },
      { title: 'Agent 最近决策', render: (_: unknown, item: ConversationSummary) =>
        <Space direction="vertical" size={0}>
          {item.latest_draft_type && <Tag>{item.latest_draft_type}</Tag>}
          <span>{item.latest_draft_content ?? '尚无自动回复'}</span>
        </Space> },
      { title: '简历发送', render: (_: unknown, item: ConversationSummary) =>
        item.resume_action_status
          ? `${item.resume_attachment_name ?? '-'} / ${item.resume_action_status}` : '-' },
    ]} />
  </Card>
}
