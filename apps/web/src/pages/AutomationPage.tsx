import {
  Alert, Button, Card, Descriptions, Divider, Form, Input, InputNumber, Select, Space, Switch,
  Table, Tag, Typography, message,
} from 'antd'
import { useEffect, useState } from 'react'
import { api } from '../api/client'
import { statusColor } from './automation-status'
import { activeWorkers, workerStatusText } from './worker-status'
import { businessLabel } from './business-labels'

interface SettingForm {
  scope_type: 'GLOBAL' | 'PLATFORM' | 'STRATEGY'; scope_key: string
  enabled: boolean; paused: boolean; auto_greet_enabled: boolean
  auto_reply_enabled: boolean
  auto_resume_enabled: boolean
  maimai_recommendation_enabled: boolean
  maimai_recommendation_resume_enabled: boolean
  emergency_stop: boolean; job_scan_enabled: boolean
  company_cooldown_hours: number; recruiter_cooldown_hours: number
  work_start_hour: number; work_end_hour: number
}

interface LlmOption { provider: string; model: string; configured: boolean }
interface LlmStatus {
  provider: string; model: string; timeout_seconds: number
  configured: boolean; options: LlmOption[]
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

const platformOptions = [
  { value: 'BOSS', label: 'BOSS 直聘' },
  { value: 'MAIMAI', label: '脉脉' },
  { value: 'LIEPIN', label: '猎聘' },
  { value: 'MOCK', label: '本地模拟' },
]

const defaultSetting: SettingForm = {
  scope_type: 'GLOBAL', scope_key: 'GLOBAL', enabled: false, paused: false,
  auto_greet_enabled: false, auto_reply_enabled: false, auto_resume_enabled: false,
  maimai_recommendation_enabled: false, maimai_recommendation_resume_enabled: false,
  emergency_stop: false, job_scan_enabled: false, company_cooldown_hours: 24,
  recruiter_cooldown_hours: 24, work_start_hour: 8, work_end_hour: 22,
}

function cursorSummary(cursor: Record<string, unknown>): string {
  const job = cursor.job_discovery
  const messageCursor = cursor.message_discovery
  const jobState = job && typeof job === 'object' ? job as Record<string, unknown> : undefined
  const messageState = messageCursor && typeof messageCursor === 'object'
    ? messageCursor as Record<string, unknown> : undefined
  const parts: string[] = []
  if (jobState) {
    parts.push(`职位 ${String(jobState.search_key ?? '-')} · 位置 ${String(jobState.scroll_position ?? 0)}`)
  }
  if (messageState) {
    parts.push(`消息位置 ${String(messageState.scroll_position ?? 0)}`)
  }
  return parts.join('；') || '尚未开始'
}

export function AutomationPage() {
  const [form] = Form.useForm<SettingForm>()
  const [llm, setLlm] = useState<LlmStatus>()
  const [selectedProvider, setSelectedProvider] = useState<string>()
  const [modelInput, setModelInput] = useState('')
  const [timeoutInput, setTimeoutInput] = useState(120)
  const [savingLlm, setSavingLlm] = useState(false)
  const [runs, setRuns] = useState<AgentRun[]>([])
  const [actions, setActions] = useState<AutomaticAction[]>([])
  const [platform, setPlatform] = useState('BOSS')
  const [strategyId, setStrategyId] = useState('')
  const [operations, setOperations] = useState<OperationsStatus>()
  const [strategies, setStrategies] = useState<StrategyOption[]>([])
  const [settings, setSettings] = useState<SettingForm[]>([])
  const [settingScopeType, setSettingScopeType] = useState<SettingForm['scope_type']>('GLOBAL')
  const [settingScopeKey, setSettingScopeKey] = useState('GLOBAL')
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
    setLlm(llmStatus); setSelectedProvider(llmStatus.provider); setModelInput(llmStatus.model)
    setTimeoutInput(llmStatus.timeout_seconds)
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
      setSelectedProvider(llmStatus.provider); setModelInput(llmStatus.model)
      setTimeoutInput(llmStatus.timeout_seconds)
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
  const showScopeSetting = (scopeType: SettingForm['scope_type'], scopeKey: string) => {
    const existing = settings.find(
      (item) => item.scope_type === scopeType && item.scope_key === scopeKey,
    )
    const globalSetting = settings.find(
      (item) => item.scope_type === 'GLOBAL' && item.scope_key === 'GLOBAL',
    )
    form.setFieldsValue({
      ...defaultSetting,
      ...(scopeType === 'GLOBAL' ? globalSetting : existing ?? globalSetting),
      scope_type: scopeType,
      scope_key: scopeKey,
    })
  }
  const selectScope = (scopeType: SettingForm['scope_type']) => {
    const scopeKey = scopeType === 'GLOBAL' ? 'GLOBAL'
      : scopeType === 'PLATFORM' ? 'BOSS' : strategies[0]?.id ?? ''
    setSettingScopeType(scopeType)
    setSettingScopeKey(scopeKey)
    showScopeSetting(scopeType, scopeKey)
  }
  const selectScopeKey = (scopeKey: string) => {
    setSettingScopeKey(scopeKey)
    showScopeSetting(settingScopeType, scopeKey)
  }
  const saveLlm = async () => {
    const provider = selectedProvider?.trim()
    const model = modelInput.trim()
    if (!provider || !model) return message.warning('请选择供应商并输入模型名称')
    setSavingLlm(true)
    try {
      const updated = await api<LlmStatus>('/system/llm-status', {
        method: 'PUT',
        body: JSON.stringify({ provider, model, timeout_seconds: timeoutInput }),
      })
      setLlm(updated)
      setSelectedProvider(updated.provider)
      setModelInput(updated.model)
      setTimeoutInput(updated.timeout_seconds)
      message.success('LLM 配置已更新，后续调用立即生效')
    } finally {
      setSavingLlm(false)
    }
  }
  const start = async (targetPlatform = platform, targetStrategyId = strategyId) => {
    if (!targetStrategyId) return message.warning('请选择已启用的求职策略')
    await api('/automation/runs', {
      method: 'POST', body: JSON.stringify({
        platform: targetPlatform, strategy_id: targetStrategyId,
      }),
    })
    await load(); message.success(`${targetPlatform} 平台任务已启动`)
  }
  const changeRun = async (run: AgentRun, operation: 'pause' | 'resume') => {
    await api(`/automation/runs/${run.id}/${operation}`, { method: 'POST' })
    await load()
  }
  const reconcile = async () => {
    await api('/automation/operations/reconciliation/run', { method: 'POST' })
    await load(); message.success('已执行一轮安全对账')
  }
  return <Space orientation="vertical" style={{ width: '100%' }} size="large">
    <Alert type="info" showIcon title="无人值守运行仍受服务端安全门禁控制"
      description="Agent 启用后直接按正式配置自动发现职位和消息、判断是否沟通并按条件发送简历；平台验证异常、未知结果和电话面试时间仍按安全规则处理。" />

    <Card title="LLM 配置状态">
      <Descriptions items={[
        { key: 'provider', label: '供应商', children: llm?.provider ?? '-' },
        { key: 'model', label: '模型', children: llm?.model ?? '-' },
        { key: 'timeout', label: '超时', children: llm ? `${llm.timeout_seconds} 秒` : '-' },
        { key: 'configured', label: '状态', children: <Tag color={llm?.configured ? 'green' : 'red'}>
          {llm?.configured ? '已配置' : '未配置'}
        </Tag> },
      ]} />
      <Space wrap>
        <Select aria-label="LLM 供应商" style={{ width: 180 }} value={selectedProvider}
          onChange={(provider) => {
            setSelectedProvider(provider)
            const option = llm?.options.find((item) => item.provider === provider)
            if (option) setModelInput(option.model)
          }}
          options={(llm?.options ?? []).map((item) => ({
            value: item.provider,
            label: `${item.provider}${item.configured ? '' : '（未配置密钥）'}`,
            disabled: !item.configured,
          }))} />
        <Input aria-label="LLM 模型" style={{ width: 280 }} value={modelInput}
          placeholder="输入模型名称"
          onChange={(event) => setModelInput(event.target.value)} />
        <InputNumber aria-label="LLM 超时时间" min={1} max={300} value={timeoutInput}
          onChange={(value) => value !== null && setTimeoutInput(value)} />
        <Typography.Text>秒</Typography.Text>
        <Button type="primary" loading={savingLlm}
          disabled={!selectedProvider || !modelInput.trim()
            || (selectedProvider === llm?.provider && modelInput.trim() === llm?.model
              && timeoutInput === llm?.timeout_seconds)}
          onClick={() => void saveLlm().catch(showRequestError)}>
          应用 LLM 配置
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

    <Card title="平台任务管理（高级）">
      <Alert type="info" showIcon style={{ marginBottom: 16 }}
        title="这里管理各平台的期望运行状态"
        description="启动平台任务后，还需 Worker 和浏览器会话正常才会实际执行。平台页面断开请到总览执行重新连接。" />
      <Space wrap style={{ marginBottom: 16 }}>
        <Select value={platform} onChange={setPlatform} options={platformOptions} />
        <Select style={{ width: 360 }} value={strategyId || undefined} onChange={setStrategyId}
          placeholder="选择已启用策略" options={strategies.map((item) => ({
            value: item.id, label: item.name,
          }))} />
        <Button type="primary" onClick={() => void start().catch(showRequestError)}>
          启动平台任务
        </Button>
        <Button onClick={() => void load().catch(showRequestError)}>刷新</Button>
      </Space>
      <Table rowKey="id" dataSource={runs} size="small" tableLayout="fixed"
        scroll={{ x: 1180 }} columns={[
        { title: '平台', dataIndex: 'platform', width: 90 },
        { title: '状态', dataIndex: 'status', width: 100, render: (value: string) => <Tag color={statusColor(value)}>{businessLabel(value)}</Tag> },
        { title: '心跳', dataIndex: 'heartbeat_at', width: 180, render: (value?: string) => value ? new Date(value).toLocaleString() : '-' },
        { title: '已处理', dataIndex: 'processed_count', width: 80 },
        { title: '已执行', dataIndex: 'action_count', width: 80 },
        { title: '失败', width: 120, render: (_: unknown, run: AgentRun) => `${run.failure_count}（连续 ${run.consecutive_failure_count}）` },
        { title: '暂停原因', dataIndex: 'pause_reason_codes', width: 160,
          render: (items: string[]) => <Typography.Text ellipsis title={items.map(businessLabel).join('、')}>
            {items.map(businessLabel).join('、') || '-'}
          </Typography.Text> },
        { title: '扫描进度', dataIndex: 'cursor', width: 230,
          render: (value: Record<string, unknown>) => <Typography.Text ellipsis title={cursorSummary(value)}>
            {cursorSummary(value)}
          </Typography.Text> },
        { title: '操作', width: 180, fixed: 'right' as const,
          render: (_: unknown, run: AgentRun) => <Space size={4} wrap>
          <Button danger disabled={run.status !== 'RUNNING'}
            onClick={() => void changeRun(run, 'pause').catch(showRequestError)}>暂停</Button>
          <Button disabled={run.status !== 'PAUSED'}
            onClick={() => void changeRun(run, 'resume').catch(showRequestError)}>恢复</Button>
          {!['RUNNING', 'PAUSED'].includes(run.status) && <Button type="primary"
            onClick={() => void start(run.platform, run.strategy_id).catch(showRequestError)}>
            重新启动
          </Button>}
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

    <Card title="自动化范围配置">
      <Alert type="info" showIcon style={{ marginBottom: 16 }}
        title="平台和策略配置与全局配置共同生效"
        description="全局配置是基础权限；平台和策略配置只能进一步暂停或收紧能力，不能绕过全局关闭的开关。关闭临时暂停后，如平台任务仍显示已暂停，请在上方点击恢复。" />
      <Space wrap align="start">
        <div>
          <Typography.Text strong>配置对象</Typography.Text>
          <div><Select aria-label="配置范围" style={{ width: 180, marginTop: 8 }}
            value={settingScopeType} onChange={selectScope} options={[
              { value: 'GLOBAL', label: '全部平台（全局）' },
              { value: 'PLATFORM', label: '指定平台' },
              { value: 'STRATEGY', label: '指定求职策略' },
            ]} /></div>
        </div>
        {settingScopeType === 'PLATFORM' && <div>
          <Typography.Text strong>选择平台</Typography.Text>
          <div><Select aria-label="配置平台" style={{ width: 180, marginTop: 8 }}
            value={settingScopeKey} onChange={selectScopeKey} options={platformOptions} /></div>
        </div>}
        {settingScopeType === 'STRATEGY' && <div>
          <Typography.Text strong>选择策略</Typography.Text>
          <div><Select aria-label="配置策略" style={{ width: 280, marginTop: 8 }}
            value={settingScopeKey || undefined} onChange={selectScopeKey}
            placeholder="选择已启用策略" options={strategies.map((item) => ({
              value: item.id, label: item.name,
            }))} /></div>
        </div>}
      </Space>
      <Divider />
      <Form form={form} layout="vertical"
      onFinish={(value) => void save(value).catch(showRequestError)}
      initialValues={defaultSetting}>
      <Form.Item name="scope_type" hidden><Input /></Form.Item>
      <Form.Item name="scope_key" hidden rules={[{ required: true }]}><Input /></Form.Item>
      <Card size="small" title="运行控制">
        <Space wrap size="large">
          <Form.Item name="enabled" label="允许运行" valuePropName="checked"
            extra="关闭后，该范围内所有自动化停止"><Switch aria-label="允许运行" /></Form.Item>
          <Form.Item name="paused" label="临时暂停" valuePropName="checked"
            extra="保留配置，暂时停止自动化"><Switch aria-label="临时暂停" /></Form.Item>
          <Form.Item name="emergency_stop" label="紧急停止" valuePropName="checked"
            extra="安全异常时立即阻止所有动作">
            <Switch aria-label="紧急停止" />
          </Form.Item>
        </Space>
      </Card>
      <Card size="small" title="自动处理能力" style={{ marginTop: 16 }}>
        <Space wrap size="large">
          <Form.Item name="job_scan_enabled" label="发现新职位" valuePropName="checked"><Switch /></Form.Item>
          <Form.Item name="auto_greet_enabled" label="主动招呼" valuePropName="checked"><Switch /></Form.Item>
          <Form.Item name="auto_reply_enabled" label="自动回复" valuePropName="checked"><Switch /></Form.Item>
          <Form.Item name="auto_resume_enabled" label="自动发送简历" valuePropName="checked"><Switch /></Form.Item>
          {(settingScopeType !== 'PLATFORM' || settingScopeKey === 'MAIMAI') && <>
            <Form.Item name="maimai_recommendation_enabled" label="处理脉脉推荐（仅脉脉）" valuePropName="checked"><Switch /></Form.Item>
            <Form.Item name="maimai_recommendation_resume_enabled" label="同意脉脉推荐简历（仅脉脉）" valuePropName="checked"><Switch /></Form.Item>
          </>}
        </Space>
      </Card>
      <Card size="small" title="运行时段与冷却" style={{ marginTop: 16 }}>
        <Space wrap size="large">
          <Form.Item name="company_cooldown_hours" label="同公司冷却（小时）"><InputNumber min={0} max={720} /></Form.Item>
          <Form.Item name="recruiter_cooldown_hours" label="同招聘人冷却（小时）"><InputNumber min={0} max={720} /></Form.Item>
          <Form.Item name="work_start_hour" label="每天开始时间（时）"><InputNumber min={0} max={23} /></Form.Item>
          <Form.Item name="work_end_hour" label="每天结束时间（时）"><InputNumber min={1} max={24} /></Form.Item>
        </Space>
      </Card>
      <Button type="primary" htmlType="submit" style={{ marginTop: 16 }}>保存配置</Button>
      </Form>
    </Card>
  </Space>
}
