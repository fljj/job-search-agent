import { Alert, Button, Card, Descriptions, Drawer, Input, List, Select, Space, Table, Tag } from 'antd'
import { useEffect, useState } from 'react'
import { api } from '../api/client'
import { businessLabel } from './business-labels'

interface DecisionSummary {
  id: string; decision: string; confidence: number; hard_rejected: boolean
  effective_job_status: string; reason: string; automation_eligible: boolean
}
interface Job {
  id: string; title: string; company_name: string; work_mode: string; location?: string
  salary_text?: string; source: string; source_url?: string
  latest_decision?: DecisionSummary
  communication?: { status: string; conversation_id?: string; action_status?: string
    failure_code?: string; reason_codes: string[] }
}
interface Strategy { id: string; name: string; enabled: boolean }
interface DecisionResult extends DecisionSummary {
  decision_version: string; prompt_version?: string; llm_provider?: string; llm_model?: string
  action_blockers: string[]; matched_evidence: string[]; uncertainties: string[]
  rejection_reasons: Array<{ rule_code: string; message: string }>
}
interface JobListResult { items: Job[]; total: number }

const DEFAULT_PAGE_SIZE = 20
const platformLabels: Record<string, string> = { BOSS: 'BOSS直聘', LIEPIN: '猎聘' }
const decisionLabels: Record<string, string> = {
  CONTACT: '建议沟通', SKIP: '不建议沟通', REVIEW: '需要进一步判断', FILTERED_OUT: '硬性排除',
}
const decisionColors: Record<string, string> = {
  CONTACT: 'green', SKIP: 'default', REVIEW: 'orange', FILTERED_OUT: 'red',
}

function linkedJobId() {
  const query = window.location.hash.split('?')[1] ?? ''
  return new URLSearchParams(query).get('job_id') ?? ''
}

async function fetchJobs(
  focusedJobId: string, strategyId: string, page: number, pageSize: number,
  workMode?: string, decision?: string, keyword?: string,
) {
  const query = new URLSearchParams({ page: String(page), page_size: String(pageSize) })
  if (focusedJobId) query.set('job_id', focusedJobId)
  if (workMode) query.set('work_mode', workMode)
  if (strategyId) query.set('strategy_id', strategyId)
  if (decision) query.set('decision', decision)
  if (keyword?.trim()) query.set('keyword', keyword.trim())
  const data = await api<JobListResult>(`/jobs?${query}`)
  if (!focusedJobId || data.items.length > 0 || !strategyId) return data
  return api<JobListResult>(`/jobs?job_id=${encodeURIComponent(focusedJobId)}`)
}

export function JobPage() {
  const [jobs, setJobs] = useState<Job[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE)
  const [loading, setLoading] = useState(false)
  const [strategies, setStrategies] = useState<Strategy[]>([])
  const [strategyId, setStrategyId] = useState('')
  const [workMode, setWorkMode] = useState<string>()
  const [decisionFilter, setDecisionFilter] = useState<string>()
  const [keyword, setKeyword] = useState('')
  const [decision, setDecision] = useState<DecisionResult>()
  const focusedJobId = linkedJobId()

  const load = async (targetPage = page, targetPageSize = pageSize) => {
    setLoading(true)
    try {
      const data = await fetchJobs(
        focusedJobId, strategyId, targetPage, targetPageSize, workMode, decisionFilter, keyword,
      )
      setJobs(data.items); setTotal(data.total)
    } finally { setLoading(false) }
  }

  useEffect(() => {
    void api<{ items: Strategy[] }>('/strategies').then(async (data) => {
      setStrategies(data.items)
      const enabled = data.items.find((item) => item.enabled)
      const selected = enabled?.id ?? ''
      setStrategyId(selected); setLoading(true)
      const jobsData = await fetchJobs(focusedJobId, selected, 1, DEFAULT_PAGE_SIZE)
      setJobs(jobsData.items); setTotal(jobsData.total); setPage(1); setLoading(false)
    })
  }, [focusedJobId])

  return <Space direction="vertical" style={{ width: '100%' }}>
    <Alert type="info" showIcon message="职位由 Agent 自动发现并作沟通决策"
      description="程序先执行硬性排除；通过后由当前 LLM 判断是否建议沟通。" />
    {focusedJobId && <Alert type="success" showIcon message="正在查看消息关联的职位" />}
    <Card title="职位流水线"><Space wrap style={{ marginBottom: 16 }}>
      <Select allowClear placeholder="求职策略" value={strategyId || undefined}
        onChange={(value) => setStrategyId(value ?? '')} style={{ minWidth: 220 }}
        options={strategies.map((item) => ({ value: item.id, label: item.name }))} />
      <Select allowClear placeholder="工作模式" value={workMode} onChange={setWorkMode}
        options={[
          { value: 'REMOTE', label: '远程' },
          { value: 'ONSITE', label: '现场办公' },
        ]} />
      <Select allowClear placeholder="沟通决策" value={decisionFilter} onChange={setDecisionFilter}
        options={Object.entries(decisionLabels).map(([value, label]) => ({ value, label }))} />
      <Input allowClear placeholder="搜索职位或公司" value={keyword}
        onChange={(event) => setKeyword(event.target.value)} onPressEnter={() => {
          setPage(1); void load(1, pageSize)
        }} style={{ width: 220 }} />
      <Button onClick={() => { setPage(1); void load(1, pageSize) }}>筛选</Button>
    </Space><Table rowKey="id" dataSource={jobs} loading={loading} scroll={{ x: 1600 }}
      tableLayout="fixed" pagination={{ current: page, pageSize, total, showSizeChanger: true,
        showTotal: (count) => `共 ${count} 个职位` }}
      onChange={(pagination) => {
        const nextPage = pagination.current ?? 1
        const nextPageSize = pagination.pageSize ?? pageSize
        setPage(nextPage); setPageSize(nextPageSize); void load(nextPage, nextPageSize)
      }} columns={[
        { title: '平台', dataIndex: 'source', width: 100,
          render: (source: string) => <Tag>{platformLabels[source] ?? source}</Tag> },
        { title: '职位', dataIndex: 'title', width: 260, ellipsis: true },
        { title: '公司', dataIndex: 'company_name', width: 220, ellipsis: true },
        { title: '模式', dataIndex: 'work_mode', width: 100,
          render: (mode: string) => <Tag>{mode === 'REMOTE' ? '远程' : '现场办公'}</Tag> },
        { title: '地点', dataIndex: 'location', width: 120, ellipsis: true },
        { title: '薪资', dataIndex: 'salary_text', width: 120, render: (value?: string) => value ?? '-' },
        { title: '沟通决策', width: 150, render: (_: unknown, job: Job) => job.latest_decision
          ? <Tag color={decisionColors[job.latest_decision.decision]}>
            {decisionLabels[job.latest_decision.decision] ?? job.latest_decision.decision}</Tag>
          : <Tag>待决策</Tag> },
        { title: '决策理由', width: 280, ellipsis: true,
          render: (_: unknown, job: Job) => job.latest_decision?.reason ?? '-' },
        { title: '沟通进度', width: 230, render: (_: unknown, job: Job) => {
          const communication = job.communication
          const reason = communication?.failure_code ?? communication?.reason_codes?.[0]
          return <Space direction="vertical" size={0} style={{ width: '100%' }}>
            <Tag>{businessLabel(communication?.status ?? 'NOT_CONTACTED')}</Tag>
            {reason && <span style={{ overflowWrap: 'anywhere' }}>{businessLabel(reason)}</span>}
            {communication?.conversation_id && <Button type="link" size="small"
              onClick={() => { window.location.hash = `messages?job_id=${job.id}` }}>查看对应消息</Button>}
          </Space>
        } },
        { title: '操作', width: 150, render: (_: unknown, job: Job) => <Space direction="vertical" size={0}>
          <Button disabled={!job.latest_decision} onClick={() => job.latest_decision
            && void api<DecisionResult>(`/decisions/${job.latest_decision.id}`).then(setDecision)}>
            查看决策依据
          </Button>
          {job.source_url ? <Button type="link" href={job.source_url} target="_blank"
            rel="noopener noreferrer">打开原职位</Button> : <Button type="link" disabled>暂无原职位链接</Button>}
        </Space> },
      ]} /></Card>
    <Drawer title="职位沟通决策" width={680} open={Boolean(decision)} onClose={() => setDecision(undefined)}>
      {decision && <Space direction="vertical" size="large" style={{ width: '100%' }}>
        <Card><Descriptions column={2} items={[
          { key: 'decision', label: '决策', children: <Tag color={decisionColors[decision.decision]}>
            {decisionLabels[decision.decision] ?? decision.decision}</Tag> },
          { key: 'confidence', label: '置信度', children: `${Math.round(decision.confidence * 100)}%` },
          { key: 'status', label: '职位状态', children: businessLabel(decision.effective_job_status) },
          { key: 'provider', label: '决策模型', children: `${decision.llm_provider ?? '-'} / ${decision.llm_model ?? '-'}` },
          { key: 'version', label: '决策/提示版本', children: `${decision.decision_version} / ${decision.prompt_version ?? '-'}` },
          { key: 'authorized', label: '允许主动沟通', children: decision.automation_eligible ? '是' : '否' },
        ]} /><Alert type={decision.hard_rejected ? 'error' : 'info'} message={decision.reason} /></Card>
        <Card title="匹配依据"><List dataSource={decision.matched_evidence}
          locale={{ emptyText: '无' }} renderItem={(item) => <List.Item>{item}</List.Item>} /></Card>
        <Card title="不确定项"><List dataSource={decision.uncertainties}
          locale={{ emptyText: '无' }} renderItem={(item) => <List.Item>{item}</List.Item>} /></Card>
        {decision.rejection_reasons.length > 0 && <Card title="硬性排除依据"><List
          dataSource={decision.rejection_reasons}
          renderItem={(item) => <List.Item>{item.message}（{businessLabel(item.rule_code)}）</List.Item>} /></Card>}
      </Space>}
    </Drawer>
  </Space>
}
