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
  emergency_stop: boolean; job_scan_enabled: boolean
  hourly_scan_limit: number; daily_scan_limit: number
  company_cooldown_hours: number; recruiter_cooldown_hours: number
  work_start_hour: number; work_end_hour: number
}

interface LlmStatus { provider: string; model: string; configured: boolean }
interface AgentRun {
  id: string; platform: string; strategy_id: string; status: string; heartbeat_at?: string
  processed_count: number; action_count: number; failure_count: number
  consecutive_failure_count: number; pause_reason_codes: string[]; cursor: Record<string, unknown>
}
interface AutomaticAction {
  id: string; action_type: string; status: string; platform: string; company: string
  job_title: string; recruiter: string; attachment_name?: string; failure_code?: string
}
interface OperationsStatus {
  database_ready: boolean; migration_revision?: string; llm_configured: boolean
  selector_version: string; executor_mode: string; calendar_provider: string
  unknown_action_count: number; pending_confirmation_count: number
  workers: Array<{ worker_id: string; status: string; heartbeat_at: string }>
  reconciliation_tasks: Array<{ id: string; action_id: string; status: string; attempt_count: number }>
  discrepancies: Array<{ code: string; action_id: string }>
}

export function AutomationPage() {
  const [form] = Form.useForm<SettingForm>()
  const [llm, setLlm] = useState<LlmStatus>()
  const [runs, setRuns] = useState<AgentRun[]>([])
  const [actions, setActions] = useState<AutomaticAction[]>([])
  const [platform, setPlatform] = useState('BOSS')
  const [strategyId, setStrategyId] = useState('')
  const [operations, setOperations] = useState<OperationsStatus>()

  const load = async () => {
    const [llmStatus, runList, actionList, operationStatus] = await Promise.all([
      api<LlmStatus>('/system/llm-status'),
      api<{ items: AgentRun[] }>('/automation/runs'),
      api<{ items: AutomaticAction[] }>('/automation/actions'),
      api<OperationsStatus>('/automation/operations/status'),
    ])
    setLlm(llmStatus); setRuns(runList.items); setActions(actionList.items); setOperations(operationStatus)
  }
  useEffect(() => {
    Promise.all([
      api<LlmStatus>('/system/llm-status'),
      api<{ items: AgentRun[] }>('/automation/runs'),
      api<{ items: AutomaticAction[] }>('/automation/actions'),
      api<OperationsStatus>('/automation/operations/status'),
    ]).then(([llmStatus, runList, actionList, operationStatus]) => {
      setLlm(llmStatus); setRuns(runList.items); setActions(actionList.items); setOperations(operationStatus)
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
  const reconcile = async () => {
    await api('/automation/operations/reconciliation/run', { method: 'POST' })
    await load(); message.success('已执行一轮安全对账')
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

    <Card title="运行自检与对账">
      <Descriptions items={[
        { key: 'db', label: '数据库/迁移', children: <Tag color={operations?.database_ready ? 'green' : 'red'}>
          {operations?.database_ready ? operations.migration_revision : '不可用'}
        </Tag> },
        { key: 'selector', label: '选择器', children: operations?.selector_version ?? '-' },
        { key: 'executor', label: '执行器', children: operations?.executor_mode ?? '-' },
        { key: 'calendar', label: '日历', children: operations?.calendar_provider ?? '-' },
        { key: 'unknown', label: '未知结果', children: operations?.unknown_action_count ?? 0 },
        { key: 'confirmations', label: '待确认', children: operations?.pending_confirmation_count ?? 0 },
        { key: 'workers', label: 'Worker', children: operations?.workers.map(
          (item) => `${item.worker_id}:${item.status}`).join('、') || '无' },
        { key: 'discrepancies', label: '审计差异', children: operations?.discrepancies.length ?? 0 },
      ]} />
      <Button disabled={!operations?.unknown_action_count} onClick={() => void reconcile()}>
        执行对账
      </Button>
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
        { title: '游标', dataIndex: 'cursor', render: (value: Record<string, unknown>) =>
          <code>{JSON.stringify(value)}</code> },
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
        hourly_limit: 1, daily_limit: 3, emergency_stop: false, job_scan_enabled: false,
        hourly_scan_limit: 100, daily_scan_limit: 500, company_cooldown_hours: 24,
        recruiter_cooldown_hours: 24, work_start_hour: 8, work_end_hour: 22 }}>
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
        <Form.Item name="job_scan_enabled" label="主动扫描职位" valuePropName="checked"><Switch /></Form.Item>
        <Form.Item name="emergency_stop" label="紧急停止" valuePropName="checked"><Switch /></Form.Item>
      </Space>
      <Space wrap>
        <Form.Item name="auto_greet_min_score" label="招呼最低分"><InputNumber min={80} max={100} /></Form.Item>
        <Form.Item name="auto_reply_min_confidence" label="回复最低置信度"><InputNumber min={.75} max={1} step={.01} /></Form.Item>
        <Form.Item name="auto_resume_min_score" label="简历最低分"><InputNumber min={60} max={100} /></Form.Item>
        <Form.Item name="hourly_limit" label="每小时上限"><InputNumber min={1} max={100} /></Form.Item>
        <Form.Item name="daily_limit" label="每日上限"><InputNumber min={1} max={1000} /></Form.Item>
        <Form.Item name="hourly_scan_limit" label="每小时扫描上限"><InputNumber min={1} max={1000} /></Form.Item>
        <Form.Item name="daily_scan_limit" label="每日扫描上限"><InputNumber min={1} max={10000} /></Form.Item>
        <Form.Item name="company_cooldown_hours" label="公司冷却（小时）"><InputNumber min={0} max={720} /></Form.Item>
        <Form.Item name="recruiter_cooldown_hours" label="招聘人冷却（小时）"><InputNumber min={0} max={720} /></Form.Item>
        <Form.Item name="work_start_hour" label="工作开始小时"><InputNumber min={0} max={23} /></Form.Item>
        <Form.Item name="work_end_hour" label="工作结束小时"><InputNumber min={1} max={24} /></Form.Item>
      </Space>
      <Button type="primary" htmlType="submit">保存配置</Button>
    </Form></Card>
  </Space>
}
