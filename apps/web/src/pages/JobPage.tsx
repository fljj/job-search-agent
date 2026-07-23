import { Alert, Button, Card, Descriptions, Drawer, Input, List, Progress, Select, Space, Table, Tag, message } from 'antd'
import { useEffect, useState } from 'react'
import { api } from '../api/client'

interface Job { id: string; title: string; company_name: string; work_mode: string; location?: string;
  latest_score?: { total_score: number; grade: string; eligibility: string; hard_rejected: boolean } }
interface ScoreDetail { dimension: string; score: number | string; max_score: number | string;
  explanation: string; evidence_refs: string[]; rule_code: string }
interface ScoreResult {
  total_score: number; grade: string; eligibility: string; hard_rejected: boolean
  effective_job_status: string; scoring_version: string; prompt_version?: string
  llm_provider?: string; llm_model?: string; llm_recommends_proactive_contact: boolean
  llm_contact_reason?: string; automation_eligible: boolean; action_blockers: string[]
  details: ScoreDetail[]; match_reasons: string[]; risk_notes: string[]
}

const template = JSON.stringify({ title: '高级Java后端工程师', company_name: '示例科技', industry: '互联网',
  location: '北京', work_mode: 'REMOTE', salary_text: '35K-40K', description: '5年以上Java经验，熟悉Spring Boot、MySQL、Redis', source_status: 'OPEN' }, null, 2)

export function JobPage() {
  const [value, setValue] = useState(template)
  const [jobs, setJobs] = useState<Job[]>([])
  const [strategyId, setStrategyId] = useState('')
  const [profileId, setProfileId] = useState('')
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
    api<{ items: Job[] }>('/jobs').then((data) => setJobs(data.items))
  }, [])
  const importJob = async () => { await api('/jobs/import', { method: 'POST', body: value }); message.success('JD 已导入'); await load() }
  const scoreJob = async (jobId: string) => {
    if (!strategyId || !profileId) return message.warning('请先填写策略 ID 和候选人资料 ID')
    const parsed = await api<{ id: string }>(`/jobs/${jobId}/parse`, { method: 'POST', body: JSON.stringify({ mode: 'RULE' }) })
    const result = await api<ScoreResult>(`/jobs/${jobId}/scores`, { method: 'POST', body: JSON.stringify({
      strategy_id: strategyId, candidate_profile_id: profileId, parsed_job_detail_id: parsed.id,
    }) })
    setScore(result)
  }
  return <Space direction="vertical" style={{ width: '100%' }}>
    <Card title="导入模拟 JD"><Input.TextArea value={value} onChange={(event) => setValue(event.target.value)} rows={10} />
      <Button type="primary" onClick={importJob} style={{ marginTop: 12 }}>导入</Button></Card>
    <Card title="职位列表"><Space style={{ marginBottom: 16 }}>
      <Input placeholder="候选人资料 ID" value={profileId} onChange={(event) => setProfileId(event.target.value)} />
      <Input placeholder="策略 ID" value={strategyId} onChange={(event) => setStrategyId(event.target.value)} />
      <Select allowClear placeholder="工作模式" value={workMode} onChange={setWorkMode} options={
        ['REMOTE', 'ONSITE', 'HYBRID', 'UNKNOWN'].map((value) => ({ value, label: value }))} />
      <Select allowClear placeholder="等级" value={grade} onChange={setGrade} options={
        ['A', 'B', 'C'].map((value) => ({ value, label: value }))} />
      <Button onClick={() => void load()}>筛选</Button>
    </Space><Table rowKey="id" dataSource={jobs} columns={[
      { title: '职位', dataIndex: 'title' }, { title: '公司', dataIndex: 'company_name' },
      { title: '模式', dataIndex: 'work_mode', render: (mode: string) => <Tag>{mode}</Tag> },
      { title: '地点', dataIndex: 'location' },
      { title: '评分', render: (_: unknown, job: Job) => job.latest_score
        ? `${job.latest_score.total_score} / ${job.latest_score.grade}` : '-' },
      { title: '操作', render: (_: unknown, job: Job) => <Button onClick={() => void scoreJob(job.id)}>解析并评分</Button> },
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
