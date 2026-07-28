import { Button, Card, Space, Table, Tag } from 'antd'
import { useEffect, useState } from 'react'
import { api } from '../api/client'
import { statusColor } from './automation-status'

interface ConversationSummary {
  id: string; platform: string; recruiter_name: string; state: string; company_name?: string
  job_id?: string; job_title?: string; latest_score?: number; latest_grade?: string
  qualification_status: 'UNKNOWN' | 'ROUGH_MATCH' | 'FULL_MATCH' | 'MISMATCH'
  qualification_evidence: string[]
  latest_draft_type?: string; latest_draft_content?: string
  latest_reply_source?: string
  latest_draft_decision?: string; latest_draft_reason_codes?: string[]
  resume_action_status?: string; resume_attachment_name?: string
}

const qualificationLabels: Record<string, string> = {
  UNKNOWN: '信息不足',
  ROUGH_MATCH: '初步匹配',
  FULL_MATCH: '完全匹配',
  MISMATCH: '不匹配',
}

const reasonLabels: Record<string, string> = {
  STRATEGY_NOT_BOUND: '尚未绑定求职策略',
  PROHIBITED_OR_FRAUD_DIRECTION: '岗位疑似违规、欺诈或明确不接受',
  COMPANY_BLACKLISTED: '公司在黑名单中',
  INDUSTRY_EXCLUDED: '行业属于排除范围',
  WORK_MODE_CONFLICT: '工作模式不符合要求',
  LOCATION_CONFLICT: '工作地点不符合要求',
  SALARY_CONFLICT: '薪资低于要求',
  JOB_DIRECTION_CONFLICT: '岗位方向不符合求职方向',
  JOB_DIRECTION_UNKNOWN: '岗位方向信息不足',
  FULL_JOB_CONTEXT_AVAILABLE: '岗位信息充分且符合策略',
  RELATED_DIRECTION_WITHOUT_CONFLICT: '岗位方向相关，暂未发现明确冲突',
  RESUME_SEND_DENIED: '简历发送条件未满足',
  INBOUND_RESUME_REQUEST_ALLOWED: '对方索要简历，可以自动发送',
  QUALIFICATION_MISMATCH: '岗位已判定不匹配',
}

const draftLabels: Record<string, string> = {
  RESUME: '发送简历',
  REPLY: '自动回复',
  GREETING: '主动打招呼',
  MISMATCH_DECLINE: '礼貌拒绝',
}

const decisionLabels: Record<string, string> = {
  ALLOW_AUTO: '允许自动执行',
  REQUIRE_CONFIRMATION: '等待人工确认',
  DENY: '不会执行',
}

const replySourceLabels: Record<string, string> = {
  RULE_TEMPLATE: '规则回复',
  KNOWLEDGE_BASE: '知识库回复',
  LLM: 'AI生成',
  HUMAN: '人工处理',
}

const actionStatusLabels: Record<string, string> = {
  PENDING: '等待执行',
  APPROVED: '已批准',
  RUNNING: '执行中',
  SUCCEEDED: '已发送',
  FAILED: '发送失败',
  FAILED_FINAL: '发送失败',
  FAILED_RETRYABLE: '等待重试',
  OUTCOME_UNKNOWN: '发送结果待确认',
  DENIED: '不会发送',
}

function displayReason(code: string) {
  return reasonLabels[code] ?? '其他规则限制'
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
  const linkedJobId = new URLSearchParams(
    window.location.hash.split('?')[1] ?? '',
  ).get('job_id')
  useEffect(() => {
    void api<{ items: ConversationSummary[] }>('/conversations').then((data) => setItems(data.items))
  }, [])
  const visibleItems = linkedJobId
    ? items.filter((item) => item.job_id === linkedJobId)
    : items
  return <Card title="招聘沟通监控" extra={<Space>
    {linkedJobId && <Button onClick={() => { window.location.hash = 'messages' }}>显示全部消息</Button>}
    <Tag color="blue">普通沟通由 Agent 自动处理</Tag>
  </Space>}>
    <Table rowKey="id" dataSource={visibleItems} columns={[
      { title: '平台', dataIndex: 'platform' },
      { title: '公司/职位', render: (_: unknown, item: ConversationSummary) =>
        <Space direction="vertical" size={0}><strong>{item.company_name ?? '-'}</strong>
          <span>{item.job_title ?? '-'}</span></Space> },
      { title: '关联职位', render: (_: unknown, item: ConversationSummary) =>
        item.job_id
          ? <Button type="link" onClick={() => {
            window.location.hash = `jobs?job_id=${item.job_id}`
          }}>查看职位</Button>
          : <Tag>职位未绑定</Tag> },
      { title: '招聘人', dataIndex: 'recruiter_name' },
      { title: '评分', render: (_: unknown, item: ConversationSummary) =>
        !item.job_id
          ? <Tag>职位未绑定</Tag>
          : item.latest_score === undefined || item.latest_score === null
          ? <Tag>待评分</Tag> : <Tag color={item.latest_score >= 80 ? 'green' : 'blue'}>
            {item.latest_score} / {item.latest_grade}</Tag> },
      { title: '入站资格', render: (_: unknown, item: ConversationSummary) =>
        <Space direction="vertical" size={0}>
          <Tag color={item.qualification_status === 'FULL_MATCH' ? 'green'
            : item.qualification_status === 'ROUGH_MATCH' ? 'blue'
              : item.qualification_status === 'MISMATCH' ? 'red' : 'default'}>
            {qualificationLabels[item.qualification_status]}
          </Tag>
          <span>{item.qualification_evidence.map(displayReason).join('；') || '-'}</span>
        </Space> },
      { title: '会话状态', dataIndex: 'state',
        render: (value: string) => <Tag color={statusColor(value)}>{value}</Tag> },
      { title: 'Agent 最近决策', render: (_: unknown, item: ConversationSummary) =>
        <Space direction="vertical" size={0}>
          {item.latest_draft_type && <Space size={4}>
            <Tag color={
              item.latest_draft_decision === 'DENY' || item.qualification_status === 'MISMATCH'
                ? 'red' : 'blue'
            }>
              {item.latest_draft_type === 'RESUME'
                && (item.latest_draft_decision === 'DENY' || item.qualification_status === 'MISMATCH')
                ? '不发送简历'
                : draftLabels[item.latest_draft_type] ?? '自动处理'}
            </Tag>
            {item.latest_draft_decision
              && <Tag>{decisionLabels[item.latest_draft_decision] ?? '已完成规则判断'}</Tag>}
            <Tag>{item.latest_reply_source
              ? replySourceLabels[item.latest_reply_source] ?? '来源未知'
              : '历史未记录来源'}</Tag>
          </Space>}
          <span>{decisionContent(item)}</span>
        </Space> },
      { title: '简历发送', render: (_: unknown, item: ConversationSummary) =>
        item.resume_action_status
          ? `${item.resume_attachment_name ?? '-'} / ${
            actionStatusLabels[item.resume_action_status] ?? '状态待确认'
          }` : '尚未创建发送动作' },
    ]} />
  </Card>
}
