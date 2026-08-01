import { Button, Card, Select, Space, Table, Tag } from 'antd'
import { useEffect, useState } from 'react'
import { api } from '../api/client'
import { statusColor } from './automation-status'
import { businessLabel } from './business-labels'

interface ConversationSummary {
  id: string; platform: string; recruiter_name: string; state: string; company_name?: string
  job_id?: string; job_title?: string; latest_decision?: string
  qualification_status: 'UNKNOWN' | 'ROUGH_MATCH' | 'FULL_MATCH' | 'MISMATCH'
  qualification_evidence: string[]
  latest_draft_type?: string; latest_draft_content?: string
  latest_reply_source?: string
  latest_draft_decision?: string; latest_draft_reason_codes?: string[]
  resume_action_status?: string; resume_attachment_name?: string
}
interface ConversationListResult { items: ConversationSummary[]; total: number }

const DEFAULT_PAGE_SIZE = 20

function displayReason(code: string) {
  return businessLabel(code)
}

function decisionContent(item: ConversationSummary) {
  if (!item.latest_draft_type) return '尚无自动决策'
  if (
    item.latest_draft_type === 'RESUME'
    && (item.latest_draft_decision === 'DENY' || item.qualification_status === 'MISMATCH')
  ) {
    const reasons = item.latest_draft_reason_codes?.filter((code) => code !== 'RESUME_SEND_DENIED')
    return `已阻止发送：${(reasons?.length ? reasons : item.qualification_evidence)
      .map(displayReason).join('；') || '发送条件未满足'}`
  }
  if (item.latest_draft_type === 'RESUME') {
    return `默认简历：${item.latest_draft_content ?? '-'}`
  }
  return item.latest_draft_content ?? '尚无自动决策'
}

export function MessagePage() {
  const [items, setItems] = useState<ConversationSummary[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE)
  const [platform, setPlatform] = useState<string>()
  const [loading, setLoading] = useState(true)
  const linkedJobId = new URLSearchParams(
    window.location.hash.split('?')[1] ?? '',
  ).get('job_id')
  const load = async (
    targetPage = page,
    targetPageSize = pageSize,
    targetPlatform = platform,
  ) => {
    const query = new URLSearchParams({
      page: String(targetPage),
      page_size: String(targetPageSize),
    })
    if (linkedJobId) query.set('job_id', linkedJobId)
    if (targetPlatform) query.set('platform', targetPlatform)
    setLoading(true)
    try {
      const data = await api<ConversationListResult>(`/conversations?${query}`)
      setItems(data.items)
      setTotal(data.total)
    } finally {
      setLoading(false)
    }
  }
  useEffect(() => {
    const query = new URLSearchParams({
      page: '1',
      page_size: String(DEFAULT_PAGE_SIZE),
    })
    if (linkedJobId) query.set('job_id', linkedJobId)
    void api<ConversationListResult>(`/conversations?${query}`).then((data) => {
      setItems(data.items)
      setTotal(data.total)
      setPage(1)
      setLoading(false)
    })
  }, [linkedJobId])
  return <Card title="招聘沟通监控" extra={<Space>
    {linkedJobId && <Button onClick={() => { window.location.hash = 'messages' }}>显示全部消息</Button>}
    <Tag color="blue">普通沟通由 Agent 自动处理</Tag>
  </Space>}>
    <Space style={{ marginBottom: 16 }}>
      <Select allowClear placeholder="全部平台" value={platform}
        style={{ minWidth: 160 }}
        options={[
          { value: 'BOSS', label: 'BOSS直聘' },
          { value: 'LIEPIN', label: '猎聘' },
        ]}
        onChange={(value) => {
          setPlatform(value)
          setPage(1)
          void load(1, pageSize, value)
        }} />
    </Space>
    <Table rowKey="id" dataSource={items} loading={loading} scroll={{ x: 1800 }}
      tableLayout="fixed"
      pagination={{
        current: page,
        pageSize,
        total,
        showSizeChanger: true,
        showTotal: (count) => `共 ${count} 条会话`,
      }}
      onChange={(pagination) => {
        const nextPage = pagination.current ?? 1
        const nextPageSize = pagination.pageSize ?? pageSize
        setPage(nextPage)
        setPageSize(nextPageSize)
        void load(nextPage, nextPageSize)
      }}
      columns={[
      { title: '平台', dataIndex: 'platform', width: 100 },
      { title: '公司/职位', width: 240, render: (_: unknown, item: ConversationSummary) =>
        <Space direction="vertical" size={0} style={{ width: '100%' }}><strong>{item.company_name ?? '-'}</strong>
          <span>{item.job_title ?? '-'}</span></Space> },
      { title: '关联职位', width: 110, render: (_: unknown, item: ConversationSummary) =>
        item.job_id
          ? <Button type="link" onClick={() => {
            window.location.hash = `jobs?job_id=${item.job_id}`
          }}>查看职位</Button>
          : <Tag>职位未绑定</Tag> },
      { title: '招聘人', dataIndex: 'recruiter_name', width: 120, ellipsis: true },
      { title: '职位决策', width: 130, render: (_: unknown, item: ConversationSummary) =>
        !item.job_id
          ? <Tag>职位未绑定</Tag>
          : !item.latest_decision
          ? <Tag>待决策</Tag> : <Tag color={item.latest_decision === 'CONTACT' ? 'green'
            : item.latest_decision === 'FILTERED_OUT' ? 'red' : 'default'}>
            {businessLabel(item.latest_decision)}</Tag> },
      { title: '入站资格', width: 260, render: (_: unknown, item: ConversationSummary) =>
        <Space direction="vertical" size={0} style={{ width: '100%' }}>
          <Tag color={item.qualification_status === 'FULL_MATCH' ? 'green'
            : item.qualification_status === 'ROUGH_MATCH' ? 'blue'
              : item.qualification_status === 'MISMATCH' ? 'red' : 'default'}>
            {businessLabel(item.qualification_status)}
          </Tag>
          <span style={{ overflowWrap: 'anywhere' }}>
            {item.qualification_evidence.map(displayReason).join('；') || '-'}
          </span>
        </Space> },
      { title: '会话状态', dataIndex: 'state', width: 130,
        render: (value: string) => <Tag color={statusColor(value)}>{value}</Tag> },
      { title: 'Agent 最近决策', width: 360, render: (_: unknown, item: ConversationSummary) =>
        <Space direction="vertical" size={0} style={{ width: '100%' }}>
          {item.latest_draft_type && <Space size={4} wrap>
            <Tag color={
              item.latest_draft_decision === 'DENY' || item.qualification_status === 'MISMATCH'
                ? 'red' : 'blue'
            }>
              {item.latest_draft_type === 'RESUME'
                && (item.latest_draft_decision === 'DENY' || item.qualification_status === 'MISMATCH')
                ? '不发送简历'
                : businessLabel(item.latest_draft_type)}
            </Tag>
            {item.latest_draft_decision
              && <Tag>{businessLabel(item.latest_draft_decision)}</Tag>}
            <Tag>{item.latest_reply_source
              ? businessLabel(item.latest_reply_source)
              : '历史未记录来源'}</Tag>
          </Space>}
          <span style={{ overflowWrap: 'anywhere' }}>{decisionContent(item)}</span>
        </Space> },
      { title: '简历发送', width: 280, ellipsis: true,
        render: (_: unknown, item: ConversationSummary) =>
        item.resume_action_status
          ? `${item.resume_attachment_name ?? '-'} / ${
            businessLabel(item.resume_action_status)
          }` : '尚未创建发送动作' },
    ]} />
  </Card>
}
