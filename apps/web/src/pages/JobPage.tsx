import { Alert, Button, Card, Descriptions, Drawer, List, Progress, Select, Space, Table, Tag } from 'antd'
import { useEffect, useState } from 'react'
import { api } from '../api/client'
import { businessLabel } from './business-labels'

interface Job { id: string; title: string; company_name: string; work_mode: string; location?: string;
  salary_text?: string; source: string; source_url?: string;
  latest_score?: { id: string; total_score: number; grade: string;
    eligibility: string; hard_rejected: boolean; effective_job_status: string };
  communication?: { status: string; conversation_id?: string; action_status?: string;
    failure_code?: string; reason_codes: string[] } }
interface Strategy { id: string; name: string; enabled: boolean }
interface ScoreDetail { dimension: string; score: number | string; max_score: number | string;
  explanation: string; evidence_refs: string[]; rule_code: string }
interface ScoreResult {
  total_score: number; grade: string; eligibility: string; hard_rejected: boolean
  effective_job_status: string; scoring_version: string; prompt_version?: string
  llm_provider?: string; llm_model?: string; llm_recommends_proactive_contact: boolean
  llm_contact_reason?: string; automation_eligible: boolean; action_blockers: string[]
  details: ScoreDetail[]; match_reasons: string[]; risk_notes: string[]
}
interface JobListResult { items: Job[]; total: number }
const DEFAULT_PAGE_SIZE = 20
const platformLabels: Record<string, string> = {
  BOSS: 'BOSS直聘',
  LIEPIN: '猎聘',
}

const dimensionLabels: Record<string, string> = {
  title: '岗位方向',
  skills: '技术栈',
  experience: '工作经历',
  location: '工作模式与地点',
  salary: '薪资',
  industry: '行业',
  management: '管理经验或岗位级别',
}

function linkedJobId() {
  const query = window.location.hash.split('?')[1] ?? ''
  return new URLSearchParams(query).get('job_id') ?? ''
}

async function fetchJobs(
  focusedJobId: string,
  selectedStrategyId: string,
  page: number,
  pageSize: number,
  workMode?: string,
  grade?: string,
) {
  const query = new URLSearchParams()
  query.set('page', String(page))
  query.set('page_size', String(pageSize))
  if (focusedJobId) query.set('job_id', focusedJobId)
  if (workMode) query.set('work_mode', workMode)
  if (selectedStrategyId) query.set('strategy_id', selectedStrategyId)
  if (grade) query.set('grade', grade)
  const data = await api<JobListResult>(`/jobs?${query}`)
  if (!focusedJobId || data.items.length > 0 || !selectedStrategyId) {
    return data
  }
  // 入站消息关联的职位可能尚未评分，不能被当前策略的评分筛选遮蔽。
  return api<JobListResult>(
    `/jobs?job_id=${encodeURIComponent(focusedJobId)}`,
  )
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
  const [grade, setGrade] = useState<string>()
  const [score, setScore] = useState<ScoreResult>()
  const focusedJobId = linkedJobId()
  const load = async (targetPage = page, targetPageSize = pageSize) => {
    setLoading(true)
    try {
      const data = await fetchJobs(
        focusedJobId, strategyId, targetPage, targetPageSize, workMode, grade,
      )
      setJobs(data.items)
      setTotal(data.total)
    } finally {
      setLoading(false)
    }
  }
  useEffect(() => {
    void api<{ items: Strategy[] }>('/strategies').then(async (strategyData) => {
      setStrategies(strategyData.items)
      const enabled = strategyData.items.find((item) => item.enabled)
      setLoading(true)
      if (enabled) {
        setStrategyId(enabled.id)
        const data = await fetchJobs(focusedJobId, enabled.id, 1, DEFAULT_PAGE_SIZE)
        setJobs(data.items)
        setTotal(data.total)
      } else {
        const data = await fetchJobs(focusedJobId, '', 1, DEFAULT_PAGE_SIZE)
        setJobs(data.items)
        setTotal(data.total)
      }
      setPage(1)
      setLoading(false)
    })
  }, [focusedJobId])
  const openScore = async (scoreId: string) => setScore(await api<ScoreResult>(`/scores/${scoreId}`))
  return <Space direction="vertical" style={{ width: '100%' }}>
    <Alert type="info" showIcon message="职位由 Agent 自动发现、解析和评分"
      description="此处只展示监控结果；硬性排除、未评分和未达到80分的职位不会被主动沟通。" />
    {focusedJobId && <Alert type="success" showIcon message="正在查看消息关联的职位"
      description="即使该职位尚未评分，也会优先显示，不受职位列表评分筛选影响。" />}
    <Card title="职位流水线"><Space wrap style={{ marginBottom: 16 }}>
      <Select allowClear placeholder="求职策略" value={strategyId || undefined} onChange={(value) => setStrategyId(value ?? '')}
        style={{ minWidth: 220 }} options={strategies.map((item) => ({ value: item.id, label: item.name }))} />
      <Select allowClear placeholder="工作模式" value={workMode} onChange={setWorkMode} options={
        ['REMOTE', 'ONSITE', 'HYBRID', 'UNKNOWN'].map((value) => ({ value, label: value }))} />
      <Select allowClear placeholder="等级" value={grade} onChange={setGrade} options={
        ['A', 'B', 'C'].map((value) => ({ value, label: value }))} />
      <Button onClick={() => {
        setPage(1)
        void load(1, pageSize)
      }}>筛选</Button>
    </Space><Table rowKey="id" dataSource={jobs} loading={loading} scroll={{ x: 1700 }}
      tableLayout="fixed"
      pagination={{
        current: page,
        pageSize,
        total,
        showSizeChanger: true,
        showTotal: (count) => `共 ${count} 个职位`,
      }}
      onChange={(pagination) => {
        const nextPage = pagination.current ?? 1
        const nextPageSize = pagination.pageSize ?? pageSize
        setPage(nextPage)
        setPageSize(nextPageSize)
        void load(nextPage, nextPageSize)
      }}
      columns={[
      { title: '平台', dataIndex: 'source', width: 100,
        render: (source: string) => <Tag>{platformLabels[source] ?? source}</Tag> },
      { title: '职位', dataIndex: 'title', width: 260, ellipsis: true },
      { title: '公司', dataIndex: 'company_name', width: 220, ellipsis: true },
      { title: '模式', dataIndex: 'work_mode', width: 100, render: (mode: string) => <Tag>{mode}</Tag> },
      { title: '地点', dataIndex: 'location', width: 120, ellipsis: true },
      { title: '薪资', dataIndex: 'salary_text', width: 120, render: (value?: string) => value ?? '-' },
      { title: '评分', width: 180, render: (_: unknown, job: Job) => job.latest_score
        ? job.latest_score.hard_rejected
          ? <Tag color="red">硬性排除（未AI评分）</Tag>
          : <Tag color={job.latest_score.total_score >= 80 ? 'green' : 'blue'}>
            {job.latest_score.total_score} / {job.latest_score.grade}</Tag>
        : <Tag>待评分</Tag> },
      { title: '硬性规则', width: 120, render: (_: unknown, job: Job) => job.latest_score
        ? <Tag color={job.latest_score.hard_rejected ? 'red' : 'green'}>
          {job.latest_score.hard_rejected ? '已排除' : '通过'}</Tag> : <Tag>待评分</Tag> },
      { title: '沟通进度', width: 240, render: (_: unknown, job: Job) => {
        const communication = job.communication
        const reason = communication?.failure_code
          ?? communication?.reason_codes?.[0]
        return <Space direction="vertical" size={0} style={{ width: '100%' }}>
          <Tag color={communication?.status === 'CONVERSATION_ACTIVE' ? 'green'
            : communication?.status === 'GREETING_RETRY_PENDING'
              || communication?.status === 'GREETING_FAILED' ? 'red' : 'blue'}>
            {businessLabel(communication?.status ?? 'NOT_CONTACTED')}
          </Tag>
          {reason && <span style={{ overflowWrap: 'anywhere' }}>{businessLabel(reason)}</span>}
          {communication?.conversation_id && <Button type="link" size="small"
            onClick={() => { window.location.hash = `messages?job_id=${job.id}` }}>
            查看对应消息
          </Button>}
        </Space>
      } },
      { title: '操作', width: 150, render: (_: unknown, job: Job) => <Space direction="vertical" size={0}>
        <Button disabled={!job.latest_score}
          onClick={() => job.latest_score && void openScore(job.latest_score.id)}>
          查看评分证据
        </Button>
        {job.source_url
          ? <Button type="link" href={job.source_url} target="_blank" rel="noopener noreferrer">
            打开原职位
          </Button>
          : <Button type="link" disabled>暂无原职位链接</Button>}
      </Space> },
    ]} /></Card>
    <Drawer title="评分详情" width={720} open={Boolean(score)} onClose={() => setScore(undefined)}>
      {score && <Space direction="vertical" size="large" style={{ width: '100%' }}>
        <Card>
          <Progress type="dashboard" percent={score.total_score} />
          <Descriptions column={2} items={[
            { key: 'grade', label: '等级', children: <Tag>{score.grade}</Tag> },
            { key: 'status', label: '职位状态', children: score.effective_job_status },
            { key: 'provider', label: '评分模型', children: `${score.llm_provider ?? '-'} / ${score.llm_model ?? '-'}` },
            { key: 'version', label: '模型/提示版本', children: `${score.scoring_version} / ${score.prompt_version ?? '-'}` },
            { key: 'recommend', label: '模型建议主动沟通', children: score.llm_recommends_proactive_contact ? '是' : '否' },
            { key: 'authorized', label: '80分程序授权', children: <Tag color={score.automation_eligible ? 'green' : 'red'}>
              {score.automation_eligible ? '允许' : '不允许'}
            </Tag> },
          ]} />
          {score.llm_contact_reason && <Alert type="info" message={score.llm_contact_reason} />}
          {score.hard_rejected && <Alert type="error" showIcon message="职位已被硬性排除"
            description={score.action_blockers.join('、')} />}
        </Card>
        <Card title="七维评分与证据"><Table rowKey="dimension" pagination={false} dataSource={score.details} columns={[
          { title: '维度', dataIndex: 'dimension',
            render: (dimension: string) => dimensionLabels[dimension] ?? dimension },
          { title: '得分', render: (_: unknown, item: ScoreDetail) => `${item.score} / ${item.max_score}` },
          { title: '规则', dataIndex: 'rule_code' }, { title: '说明', dataIndex: 'explanation' },
          { title: '证据', dataIndex: 'evidence_refs', render: (items: string[]) => items.join('、') || '-' },
        ]} /></Card>
        <Card title="匹配理由"><List dataSource={score.match_reasons} renderItem={(item) => <List.Item>{item}</List.Item>} /></Card>
        <Card title="风险提示"><List dataSource={score.risk_notes} renderItem={(item) => <List.Item>{item}</List.Item>} /></Card>
      </Space>}
    </Drawer>
  </Space>
}
