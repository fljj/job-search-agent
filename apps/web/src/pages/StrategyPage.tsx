import { Button, Card, Input, Select, Space, message } from 'antd'
import { useEffect, useState } from 'react'
import { api } from '../api/client'

interface Strategy { id: string; name: string; version: number; [key: string]: unknown }

const template = JSON.stringify({
  candidate_profile_id: '请填写候选人资料 ID', name: 'Java 后端岗位', enabled: true, priority: 100,
  title_rules: [{ rule_type: 'INCLUDE', pattern: 'Java后端', score: 15 }],
  accepted_seniority_levels: ['SENIOR', 'LEAD', 'ARCHITECT'],
  work_mode_rules: [
    { work_mode: 'REMOTE', enabled: true, allowed_locations: [], location_restricted: false, score: 15, unknown_score: 8 },
    { work_mode: 'ONSITE', enabled: true, allowed_locations: ['济南'], location_restricted: true, score: 15, unknown_score: 8 },
  ],
  salary_rules: [], industry_rules: [], company_blacklist: [], accept_outsourcing: false,
  accept_headhunter: true, headhunter_score_cap: null, max_posted_days: 30,
  core_required_skills: ['Java'], version: 1,
}, null, 2)

export function StrategyPage() {
  const [value, setValue] = useState(template)
  const [strategies, setStrategies] = useState<Strategy[]>([])
  const [selectedId, setSelectedId] = useState<string>()
  const load = () => api<{ items: Strategy[] }>('/strategies').then((data) => setStrategies(data.items))
  useEffect(() => { void load() }, [])
  const select = async (id: string) => {
    const strategy = await api<Strategy>(`/strategies/${id}`)
    setSelectedId(id)
    const { id: _id, ...payload } = strategy
    void _id
    setValue(JSON.stringify(payload, null, 2))
  }
  const save = async () => {
    const path = selectedId ? `/strategies/${selectedId}` : '/strategies'
    await api(path, { method: selectedId ? 'PUT' : 'POST', body: value })
    message.success(selectedId ? '策略已更新' : '策略已创建')
    setSelectedId(undefined)
    setValue(template)
    await load()
  }
  return <Card title="求职策略"><Space direction="vertical" style={{ width: '100%' }}>
    <Select placeholder="选择已有策略进行编辑" allowClear value={selectedId} onChange={(id) => id ? void select(id) : setSelectedId(undefined)}
      options={strategies.map((item) => ({ value: item.id, label: `${item.name} v${item.version}` }))} />
    <Input.TextArea value={value} onChange={(event) => setValue(event.target.value)} rows={20} />
    <Button type="primary" onClick={save}>{selectedId ? '更新策略' : '创建策略'}</Button>
  </Space></Card>
}
