import {
  Alert, Button, Card, Descriptions, Form, Input, InputNumber, Select, Space, Switch,
  Table, Tag, message,
} from 'antd'
import { useEffect, useState } from 'react'
import { api } from '../api/client'
import { statusColor } from './automation-status'

interface SettingForm {
  scope_type: 'GLOBAL' | 'PLATFORM' | 'STRATEGY'; scope_key: string
  enabled: boolean; paused: boolean; auto_greet_enabled: boolean
  auto_greet_min_score: number; auto_reply_enabled: boolean
  auto_reply_min_confidence: number; auto_resume_enabled: boolean
  auto_resume_min_score: number; hourly_limit: number; daily_limit: number
}

interface LlmStatus { provider: string; model: string; configured: boolean }
interface AgentRun {
  id: string; platform: string; strategy_id: string; status: string; heartbeat_at?: string
  processed_count: number; action_count: number; failure_count: number
  consecutive_failure_count: number; pause_reason_codes: string[]
}
interface AutomaticAction {
  id: string; action_type: string; status: string; platform: string; company: string
  job_title: string; recruiter: string; attachment_name?: string; failure_code?: string
}

export function AutomationPage() {
  const [form] = Form.useForm<SettingForm>()
  const [llm, setLlm] = useState<LlmStatus>()
  const [runs, setRuns] = useState<AgentRun[]>([])
  const [actions, setActions] = useState<AutomaticAction[]>([])
  const [platform, setPlatform] = useState('BOSS')
  const [strategyId, setStrategyId] = useState('')

  const load = async () => {
    const [llmStatus, runList, actionList] = await Promise.all([
      api<LlmStatus>('/system/llm-status'),
      api<{ items: AgentRun[] }>('/automation/runs'),
      api<{ items: AutomaticAction[] }>('/automation/actions'),
    ])
    setLlm(llmStatus); setRuns(runList.items); setActions(actionList.items)
  }
  useEffect(() => {
    Promise.all([
      api<LlmStatus>('/system/llm-status'),
      api<{ items: AgentRun[] }>('/automation/runs'),
      api<{ items: AutomaticAction[] }>('/automation/actions'),
    ]).then(([llmStatus, runList, actionList]) => {
      setLlm(llmStatus); setRuns(runList.items); setActions(actionList.items)
    })
  }, [])

  const save = async (values: SettingForm) => {
    await api('/automation/settings', { method: 'PUT', body: JSON.stringify(values) })
    message.success('自动化配置已保存')
  }
  const start = async () => {
    if (!strategyId) return message.warning('请填写已启用的策略 ID')
    await api('/automation/runs', {
      method: 'POST', body: JSON.stringify({ platform, strategy_id: strategyId }),
    })
    await load(); message.success('Agent 运行已创建')
  }
  const changeRun = async (run: AgentRun, operation: 'pause' | 'resume') => {
    await api(`/automation/runs/${run.id}/${operation}`, { method: 'POST' })
    await load()
  }

  return <Space direction="vertical" style={{ width: '100%' }} size="large">
    <Alert type="warning" showIcon message="真实联调必须逐步人工放行"
      description="控制台加载、保存配置和启动运行均不会自动扩大真实联调范围。请依次完成单次 LLM、单职位只读、一次手动招呼、1次/小时且3次/日自动招呼、单对话回复、单次简历和时间确认验证。" />

    <Card title="LLM 配置状态">
      <Descriptions items={[
        { key: 'provider', label: '供应商', children: llm?.provider ?? '-' },
        { key: 'model', label: '模型', children: llm?.model ?? '-' },
        { key: 'configured', label: '状态', children: <Tag color={llm?.configured ? 'green' : 'red'}>
          {llm?.configured ? '已配置' : '未配置'}
        </Tag> },
      ]} />
    </Card>

    <Card title="Agent 运行">
      <Space wrap style={{ marginBottom: 16 }}>
        <Select value={platform} onChange={setPlatform} options={[
          { value: 'BOSS', label: 'BOSS' }, { value: 'MAIMAI', label: '脉脉' },
          { value: 'MOCK', label: '本地模拟' },
        ]} />
        <Input style={{ width: 360 }} value={strategyId} onChange={(event) => setStrategyId(event.target.value)}
          placeholder="已启用的策略 ID" />
        <Button type="primary" onClick={() => void start()}>启动</Button>
        <Button onClick={() => void load()}>刷新</Button>
      </Space>
      <Table rowKey="id" dataSource={runs} columns={[
        { title: '平台', dataIndex: 'platform' },
        { title: '状态', dataIndex: 'status', render: (value: string) => <Tag color={statusColor(value)}>{value}</Tag> },
        { title: '心跳', dataIndex: 'heartbeat_at', render: (value?: string) => value ? new Date(value).toLocaleString() : '-' },
        { title: '已处理', dataIndex: 'processed_count' }, { title: '已执行', dataIndex: 'action_count' },
        { title: '失败', render: (_: unknown, run: AgentRun) => `${run.failure_count}（连续 ${run.consecutive_failure_count}）` },
        { title: '暂停原因', dataIndex: 'pause_reason_codes', render: (items: string[]) => items.join('、') || '-' },
        { title: '操作', render: (_: unknown, run: AgentRun) => <Space>
          <Button danger disabled={run.status !== 'RUNNING'} onClick={() => void changeRun(run, 'pause')}>暂停</Button>
          <Button disabled={run.status !== 'PAUSED'} onClick={() => void changeRun(run, 'resume')}>恢复</Button>
        </Space> },
      ]} />
    </Card>

    <Card title="普通自动动作（无需时间确认）">
      <Table rowKey="id" dataSource={actions} columns={[
        { title: '类型', dataIndex: 'action_type' }, { title: '平台', dataIndex: 'platform' },
        { title: '公司/职位', render: (_: unknown, item: AutomaticAction) => `${item.company} / ${item.job_title}` },
        { title: '招聘人', dataIndex: 'recruiter' },
        { title: '附件', dataIndex: 'attachment_name', render: (value?: string) => value ?? '-' },
        { title: '结果', dataIndex: 'status', render: (value: string) => <Tag color={statusColor(value)}>{value}</Tag> },
        { title: '失败原因', dataIndex: 'failure_code', render: (value?: string) => value ?? '-' },
      ]} />
    </Card>

    <Card title="自动化范围配置"><Form form={form} layout="vertical" onFinish={(value) => void save(value)}
      initialValues={{ scope_type: 'GLOBAL', scope_key: 'GLOBAL', enabled: false, paused: false,
        auto_greet_enabled: false, auto_greet_min_score: 80, auto_reply_enabled: false,
        auto_reply_min_confidence: .9, auto_resume_enabled: false, auto_resume_min_score: 60,
        hourly_limit: 1, daily_limit: 3 }}>
      <Form.Item name="scope_type" label="范围"><Select options={[
        { value: 'GLOBAL', label: '全局' }, { value: 'PLATFORM', label: '平台' },
        { value: 'STRATEGY', label: '策略' },
      ]} /></Form.Item>
      <Form.Item name="scope_key" label="范围标识" rules={[{ required: true }]}><Input /></Form.Item>
      <Space wrap>
        <Form.Item name="enabled" label="启用" valuePropName="checked"><Switch /></Form.Item>
        <Form.Item name="paused" label="暂停" valuePropName="checked"><Switch /></Form.Item>
        <Form.Item name="auto_greet_enabled" label="自动招呼" valuePropName="checked"><Switch /></Form.Item>
        <Form.Item name="auto_reply_enabled" label="自动回复" valuePropName="checked"><Switch /></Form.Item>
        <Form.Item name="auto_resume_enabled" label="自动简历" valuePropName="checked"><Switch /></Form.Item>
      </Space>
      <Space wrap>
        <Form.Item name="auto_greet_min_score" label="招呼最低分"><InputNumber min={80} max={100} /></Form.Item>
        <Form.Item name="auto_reply_min_confidence" label="回复最低置信度"><InputNumber min={.75} max={1} step={.01} /></Form.Item>
        <Form.Item name="auto_resume_min_score" label="简历最低分"><InputNumber min={60} max={100} /></Form.Item>
        <Form.Item name="hourly_limit" label="每小时上限"><InputNumber min={1} max={100} /></Form.Item>
        <Form.Item name="daily_limit" label="每日上限"><InputNumber min={1} max={1000} /></Form.Item>
      </Space>
      <Button type="primary" htmlType="submit">保存配置</Button>
    </Form></Card>
  </Space>
}
