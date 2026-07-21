import { Button, Card, Drawer, Input, Select, Space, Table, Tag, message } from 'antd'
import { useEffect, useState } from 'react'
import { api } from '../api/client'

interface Job { id: string; title: string; company_name: string; work_mode: string; location?: string;
  latest_score?: { total_score: number; grade: string; eligibility: string; hard_rejected: boolean } }

const template = JSON.stringify({ title: '高级Java后端工程师', company_name: '示例科技', industry: '互联网',
  location: '北京', work_mode: 'REMOTE', salary_text: '35K-40K', description: '5年以上Java经验，熟悉Spring Boot、MySQL、Redis', source_status: 'OPEN' }, null, 2)

export function JobPage() {
  const [value, setValue] = useState(template)
  const [jobs, setJobs] = useState<Job[]>([])
  const [strategyId, setStrategyId] = useState('')
  const [profileId, setProfileId] = useState('')
  const [workMode, setWorkMode] = useState<string>()
  const [grade, setGrade] = useState<string>()
  const [score, setScore] = useState<Record<string, unknown>>()
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
    const result = await api<Record<string, unknown>>(`/jobs/${jobId}/scores`, { method: 'POST', body: JSON.stringify({
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
      <pre style={{ whiteSpace: 'pre-wrap' }}>{JSON.stringify(score, null, 2)}</pre>
    </Drawer>
  </Space>
}
