import { Button, Card, Input, InputNumber, Select, Space, Switch, message } from 'antd'
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
  accept_part_time: false,
  accept_headhunter: true, headhunter_score_cap: null, max_posted_days: 30,
  reject_full_time_bachelor_required: false,
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
  const updateField = (field: string, fieldValue: unknown) => {
    try {
      const parsed = JSON.parse(value) as Record<string, unknown>
      setValue(JSON.stringify({ ...parsed, [field]: fieldValue }, null, 2))
    } catch {
      message.error('请先修复高级 JSON 格式')
    }
  }
  const parsed = (() => {
    try { return JSON.parse(value) as Record<string, unknown> } catch { return {} }
  })()
  return <Card title="求职策略"><Space direction="vertical" style={{ width: '100%' }}>
    <Select placeholder="选择已有策略进行编辑" allowClear value={selectedId} onChange={(id) => id ? void select(id) : setSelectedId(undefined)}
      options={strategies.map((item) => ({ value: item.id, label: `${item.name} v${item.version}` }))} />
    <Card size="small" title="常用设置"><Space wrap>
      <Input value={String(parsed.name ?? '')} placeholder="策略名称"
        onChange={(event) => updateField('name', event.target.value)} />
      <InputNumber value={Number(parsed.priority ?? 100)} min={1} max={1000}
        addonBefore="优先级" onChange={(next) => updateField('priority', next)} />
      <Space>启用<Switch checked={Boolean(parsed.enabled)}
        onChange={(checked) => updateField('enabled', checked)} /></Space>
    </Space></Card>
    <Card size="small" title="高级 JSON（地点、薪资和工作模式由服务端再次校验）">
      <Input.TextArea value={value} onChange={(event) => setValue(event.target.value)} rows={18} />
    </Card>
    <Button type="primary" onClick={save}>{selectedId ? '更新策略' : '创建策略'}</Button>
  </Space></Card>
}
