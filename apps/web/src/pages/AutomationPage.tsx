import {
  Alert, Button, Card, Descriptions, Form, Input, InputNumber, Select, Space, Switch,
  Table, Tag, message,
} from 'antd'
import { useEffect, useState } from 'react'
import { api } from '../api/client'
import { statusColor } from './automation-status'
import { activeWorkers, workerStatusText } from './worker-status'
import { businessLabel } from './business-labels'

interface SettingForm {
  scope_type: 'GLOBAL' | 'PLATFORM' | 'STRATEGY'; scope_key: string
  enabled: boolean; paused: boolean; auto_greet_enabled: boolean
  auto_greet_min_score: number; auto_reply_enabled: boolean
  auto_resume_enabled: boolean
  maimai_recommendation_enabled: boolean
  maimai_recommendation_resume_enabled: boolean
  emergency_stop: boolean; job_scan_enabled: boolean
  company_cooldown_hours: number; recruiter_cooldown_hours: number
  work_start_hour: number; work_end_hour: number
}

interface LlmOption { provider: string; model: string; configured: boolean }
interface LlmStatus {
  provider: string; model: string; configured: boolean; options: LlmOption[]
}
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
interface StrategyOption { id: string; name: string; enabled: boolean }

export function AutomationPage() {
  const [form] = Form.useForm<SettingForm>()
  const [llm, setLlm] = useState<LlmStatus>()
  const [selectedLlm, setSelectedLlm] = useState<string>()
  const [savingLlm, setSavingLlm] = useState(false)
  const [runs, setRuns] = useState<AgentRun[]>([])
  const [actions, setActions] = useState<AutomaticAction[]>([])
  const [platform, setPlatform] = useState('BOSS')
  const [strategyId, setStrategyId] = useState('')
  const [operations, setOperations] = useState<OperationsStatus>()
  const [strategies, setStrategies] = useState<StrategyOption[]>([])
  const [settings, setSettings] = useState<SettingForm[]>([])
  const currentWorkers = activeWorkers(operations?.workers)
  const showRequestError = (error: unknown) =>
    message.error(error instanceof Error ? error.message : '操作失败，请稍后重试')

  const load = async () => {
    const [llmStatus, runList, actionList, operationStatus, strategyList, settingList] = await Promise.all([
      api<LlmStatus>('/system/llm-status'),
      api<{ items: AgentRun[] }>('/automation/runs'),
      api<{ items: AutomaticAction[] }>('/automation/actions'),
      api<OperationsStatus>('/automation/operations/status'),
      api<{ items: StrategyOption[] }>('/strategies?enabled=true'),
      api<{ items: SettingForm[] }>('/automation/settings'),
    ])
    setLlm(llmStatus); setSelectedLlm(`${llmStatus.provider}:${llmStatus.model}`)
    setRuns(runList.items); setActions(actionList.items)
    setOperations(operationStatus)
    setStrategies(strategyList.items)
    setSettings(settingList.items)
  }
  useEffect(() => {
    Promise.all([
      api<LlmStatus>('/system/llm-status'),
      api<{ items: AgentRun[] }>('/automation/runs'),
      api<{ items: AutomaticAction[] }>('/automation/actions'),
      api<OperationsStatus>('/automation/operations/status'),
      api<{ items: StrategyOption[] }>('/strategies?enabled=true'),
      api<{ items: SettingForm[] }>('/automation/settings'),
    ]).then(([llmStatus, runList, actionList, operationStatus, strategyList, settingList]) => {
      setLlm(llmStatus); setRuns(runList.items); setActions(actionList.items)
      setSelectedLlm(`${llmStatus.provider}:${llmStatus.model}`)
      setOperations(operationStatus)
      setStrategies(strategyList.items)
      setSettings(settingList.items)
      const globalSetting = settingList.items.find(
        (item) => item.scope_type === 'GLOBAL' && item.scope_key === 'GLOBAL',
      )
      if (globalSetting) form.setFieldsValue(globalSetting)
      if (strategyList.items.length === 1) setStrategyId(strategyList.items[0].id)
    })
  }, [form])

  const save = async (values: SettingForm) => {
    const updated = await api<SettingForm>('/automation/settings', {
      method: 'PUT', body: JSON.stringify(values),
    })
    setSettings((current) => [
      ...current.filter((item) =>
        item.scope_type !== updated.scope_type || item.scope_key !== updated.scope_key),
      updated,
    ])
    message.success('自动化配置已保存')
  }
  const selectScope = (scopeType: SettingForm['scope_type']) => {
    const scopeKey = scopeType === 'GLOBAL' ? 'GLOBAL'
      : scopeType === 'PLATFORM' ? platform : strategyId
    const existing = settings.find(
      (item) => item.scope_type === scopeType && item.scope_key === scopeKey,
    )
    form.setFieldsValue(existing ?? { scope_type: scopeType, scope_key: scopeKey })
  }
  const saveLlm = async () => {
    const option = llm?.options.find(
      (item) => `${item.provider}:${item.model}` === selectedLlm,
    )
    if (!option) return
    setSavingLlm(true)
    try {
      const updated = await api<LlmStatus>('/system/llm-status', {
        method: 'PUT',
        body: JSON.stringify({ provider: option.provider, model: option.model }),
      })
      setLlm(updated)
      message.success(`已切换到 ${updated.provider} / ${updated.model}，后续调用立即生效`)
    } finally {
      setSavingLlm(false)
    }
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
    <Alert type="info" showIcon message="无人值守运行仍受服务端安全门禁控制"
      description="Agent 启用后直接按正式配置自动发现职位和消息、评分、沟通及按条件发送简历；平台验证异常、未知结果和电话面试时间仍按安全规则处理。" />

    <Card title="LLM 配置状态">
      <Descriptions items={[
        { key: 'provider', label: '供应商', children: llm?.provider ?? '-' },
        { key: 'model', label: '模型', children: llm?.model ?? '-' },
        { key: 'configured', label: '状态', children: <Tag color={llm?.configured ? 'green' : 'red'}>
          {llm?.configured ? '已配置' : '未配置'}
        </Tag> },
      ]} />
      <Space wrap>
        <Select style={{ width: 320 }} value={selectedLlm}
          onChange={setSelectedLlm}
          options={(llm?.options ?? []).map((item) => ({
            value: `${item.provider}:${item.model}`,
            label: `${item.provider} / ${item.model}${item.configured ? '' : '（未配置密钥）'}`,
            disabled: !item.configured,
          }))} />
        <Button type="primary" loading={savingLlm}
          disabled={!selectedLlm || selectedLlm === `${llm?.provider}:${llm?.model}`}
          onClick={() => void saveLlm().catch(showRequestError)}>
          切换模型
        </Button>
        <span>API Key 仅从环境变量读取，不会保存到数据库。</span>
      </Space>
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
        { key: 'workers', label: 'Worker', children: <Tag color={
          currentWorkers.length > 1 ? 'red'
            : currentWorkers[0]?.status === 'STALE' ? 'orange'
              : currentWorkers.length === 1 ? 'green' : 'default'
        }>{workerStatusText(operations?.workers)}</Tag> },
        { key: 'discrepancies', label: '审计差异', children: operations?.discrepancies.length ?? 0 },
      ]} />
      <Button disabled={!operations?.unknown_action_count}
        onClick={() => void reconcile().catch(showRequestError)}>
        执行对账
      </Button>
    </Card>

    <Card title="Agent 运行">
      <Space wrap style={{ marginBottom: 16 }}>
        <Select value={platform} onChange={setPlatform} options={[
          { value: 'BOSS', label: 'BOSS' }, { value: 'MAIMAI', label: '脉脉' },
          { value: 'MOCK', label: '本地模拟' },
        ]} />
        <Select style={{ width: 360 }} value={strategyId || undefined} onChange={setStrategyId}
          placeholder="选择已启用策略" options={strategies.map((item) => ({
            value: item.id, label: item.name,
          }))} />
        <Button type="primary" onClick={() => void start().catch(showRequestError)}>启动</Button>
        <Button onClick={() => void load().catch(showRequestError)}>刷新</Button>
      </Space>
      <Table rowKey="id" dataSource={runs} columns={[
        { title: '平台', dataIndex: 'platform' },
        { title: '状态', dataIndex: 'status', render: (value: string) => <Tag color={statusColor(value)}>{businessLabel(value)}</Tag> },
        { title: '心跳', dataIndex: 'heartbeat_at', render: (value?: string) => value ? new Date(value).toLocaleString() : '-' },
        { title: '已处理', dataIndex: 'processed_count' }, { title: '已执行', dataIndex: 'action_count' },
        { title: '失败', render: (_: unknown, run: AgentRun) => `${run.failure_count}（连续 ${run.consecutive_failure_count}）` },
        { title: '暂停原因', dataIndex: 'pause_reason_codes', render: (items: string[]) => items.map(businessLabel).join('、') || '-' },
        { title: '游标', dataIndex: 'cursor', render: (value: Record<string, unknown>) =>
          <code>{JSON.stringify(value)}</code> },
        { title: '操作', render: (_: unknown, run: AgentRun) => <Space>
          <Button danger disabled={run.status !== 'RUNNING'}
            onClick={() => void changeRun(run, 'pause').catch(showRequestError)}>暂停</Button>
          <Button disabled={run.status !== 'PAUSED'}
            onClick={() => void changeRun(run, 'resume').catch(showRequestError)}>恢复</Button>
        </Space> },
      ]} />
    </Card>

    <Card title="自动动作审计（无需逐条确认）">
      <Table rowKey="id" dataSource={actions} columns={[
        { title: '类型', dataIndex: 'action_type', render: businessLabel }, { title: '平台', dataIndex: 'platform' },
        { title: '公司/职位', render: (_: unknown, item: AutomaticAction) => `${item.company} / ${item.job_title}` },
        { title: '招聘人', dataIndex: 'recruiter' },
        { title: '附件', dataIndex: 'attachment_name', render: (value?: string) => value ?? '-' },
        { title: '结果', dataIndex: 'status', render: (value: string) => <Tag color={statusColor(value)}>{businessLabel(value)}</Tag> },
        { title: '失败原因', dataIndex: 'failure_code', render: (value?: string) => value ? businessLabel(value) : '-' },
      ]} />
    </Card>

    <Card title="自动化范围配置"><Form form={form} layout="vertical"
      onFinish={(value) => void save(value).catch(showRequestError)}
      initialValues={{ scope_type: 'GLOBAL', scope_key: 'GLOBAL', enabled: false, paused: false,
        auto_greet_enabled: false, auto_greet_min_score: 80, auto_reply_enabled: false,
        auto_resume_enabled: false, maimai_recommendation_enabled: false,
        maimai_recommendation_resume_enabled: false,
        emergency_stop: false, job_scan_enabled: false, company_cooldown_hours: 24,
        recruiter_cooldown_hours: 24, work_start_hour: 8, work_end_hour: 22 }}>
      <Form.Item name="scope_type" label="范围"><Select onChange={selectScope} options={[
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
        <Form.Item name="maimai_recommendation_enabled" label="脉脉推荐处理" valuePropName="checked"><Switch /></Form.Item>
        <Form.Item name="maimai_recommendation_resume_enabled" label="脉脉推荐同意简历" valuePropName="checked"><Switch /></Form.Item>
        <Form.Item name="job_scan_enabled" label="主动扫描职位" valuePropName="checked"><Switch /></Form.Item>
        <Form.Item name="emergency_stop" label="紧急停止" valuePropName="checked">
          <Switch aria-label="紧急停止" />
        </Form.Item>
      </Space>
      <Space wrap>
        <Form.Item name="auto_greet_min_score" label="招呼最低分"><InputNumber min={80} max={100} /></Form.Item>
        <Form.Item name="company_cooldown_hours" label="公司冷却（小时）"><InputNumber min={0} max={720} /></Form.Item>
        <Form.Item name="recruiter_cooldown_hours" label="招聘人冷却（小时）"><InputNumber min={0} max={720} /></Form.Item>
        <Form.Item name="work_start_hour" label="工作开始小时"><InputNumber min={0} max={23} /></Form.Item>
        <Form.Item name="work_end_hour" label="工作结束小时"><InputNumber min={1} max={24} /></Form.Item>
      </Space>
      <Button type="primary" htmlType="submit">保存配置</Button>
    </Form></Card>
  </Space>
}
