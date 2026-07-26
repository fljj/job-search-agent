import { Alert, Button, Card, Descriptions, Drawer, List, Progress, Select, Space, Table, Tag } from 'antd'
import { useEffect, useState } from 'react'
import { api } from '../api/client'

interface Job { id: string; title: string; company_name: string; work_mode: string; location?: string;
  salary_text?: string; source: string; latest_score?: { id: string; total_score: number; grade: string;
    eligibility: string; hard_rejected: boolean; effective_job_status: string } }
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

export function JobPage() {
  const [jobs, setJobs] = useState<Job[]>([])
  const [strategies, setStrategies] = useState<Strategy[]>([])
  const [strategyId, setStrategyId] = useState('')
  const [workMode, setWorkMode] = useState<string>()
  const [grade, setGrade] = useState<string>()
  const [score, setScore] = useState<ScoreResult>()
  const load = () => {
    const query = new URLSearchParams()
    if (workMode) query.set('work_mode', workMode)
    if (strategyId) query.set('strategy_id', strategyId)
    if (grade) query.set('grade', grade)
    return api<{ items: Job[] }>(`/jobs?${query}`).then((data) => setJobs(data.items))
  }
  useEffect(() => {
    void api<{ items: Strategy[] }>('/strategies').then(async (strategyData) => {
      setStrategies(strategyData.items)
      const enabled = strategyData.items.find((item) => item.enabled)
      if (enabled) {
        setStrategyId(enabled.id)
        const jobData = await api<{ items: Job[] }>(
          `/jobs?strategy_id=${encodeURIComponent(enabled.id)}`,
        )
        setJobs(jobData.items)
      } else {
        const jobData = await api<{ items: Job[] }>('/jobs')
        setJobs(jobData.items)
      }
    })
  }, [])
  const openScore = async (scoreId: string) => setScore(await api<ScoreResult>(`/scores/${scoreId}`))
  return <Space direction="vertical" style={{ width: '100%' }}>
    <Alert type="info" showIcon message="职位由 Agent 自动发现、解析和评分"
      description="此处只展示监控结果；硬性排除、未评分和未达到80分的职位不会被主动沟通。" />
    <Card title="职位流水线"><Space wrap style={{ marginBottom: 16 }}>
      <Select allowClear placeholder="求职策略" value={strategyId || undefined} onChange={(value) => setStrategyId(value ?? '')}
        style={{ minWidth: 220 }} options={strategies.map((item) => ({ value: item.id, label: item.name }))} />
      <Select allowClear placeholder="工作模式" value={workMode} onChange={setWorkMode} options={
        ['REMOTE', 'ONSITE', 'HYBRID', 'UNKNOWN'].map((value) => ({ value, label: value }))} />
      <Select allowClear placeholder="等级" value={grade} onChange={setGrade} options={
        ['A', 'B', 'C'].map((value) => ({ value, label: value }))} />
      <Button onClick={() => void load()}>筛选</Button>
    </Space><Table rowKey="id" dataSource={jobs} columns={[
      { title: '职位', dataIndex: 'title' }, { title: '公司', dataIndex: 'company_name' },
      { title: '模式', dataIndex: 'work_mode', render: (mode: string) => <Tag>{mode}</Tag> },
      { title: '地点', dataIndex: 'location' },
      { title: '薪资', dataIndex: 'salary_text', render: (value?: string) => value ?? '-' },
      { title: '评分', render: (_: unknown, job: Job) => job.latest_score
        ? <Tag color={job.latest_score.total_score >= 80 ? 'green' : 'blue'}>
          {job.latest_score.total_score} / {job.latest_score.grade}</Tag> : <Tag>待评分</Tag> },
      { title: '硬性规则', render: (_: unknown, job: Job) => job.latest_score
        ? <Tag color={job.latest_score.hard_rejected ? 'red' : 'green'}>
          {job.latest_score.hard_rejected ? '已排除' : '通过'}</Tag> : <Tag>待评分</Tag> },
      { title: '操作', render: (_: unknown, job: Job) => <Button
        disabled={!job.latest_score} onClick={() => job.latest_score && void openScore(job.latest_score.id)}>
        查看评分证据</Button> },
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
          { title: '维度', dataIndex: 'dimension' },
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
