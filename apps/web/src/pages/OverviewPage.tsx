import { Alert, Button, Card, Col, Descriptions, Row, Space, Statistic, Tag, message } from 'antd'
import { useEffect, useState } from 'react'
import { api } from '../api/client'
import { statusColor } from './automation-status'
import { activeRuns, agentStatusText, canReconnectRun, runStatusText } from './run-summary'
import { activeWorkers, workerStatusText } from './worker-status'

interface Run {
  id: string; platform: string; status: string; processed_count: number
  action_count: number; failure_count: number; pause_reason_codes: string[]
}
interface Action { status: string; action_type: string }
interface Conversation { state: string; latest_score?: number }
interface Operations {
  database_ready: boolean; llm_configured: boolean; unknown_action_count: number
  pending_confirmation_count: number; workers: Array<{ worker_id: string; status: string }>
  discrepancies: unknown[]
  llm_circuit: {
    status: 'CLOSED' | 'OPEN' | 'PROBING'; provider: string; model: string
    failure_code?: string; probe_attempt_count: number; next_probe_at?: string
  }
}
export function OverviewPage() {
  const [runs, setRuns] = useState<Run[]>([])
  const [actions, setActions] = useState<Action[]>([])
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [operations, setOperations] = useState<Operations>()
  const [loadError, setLoadError] = useState<string>()
  const [reconnectingRunId, setReconnectingRunId] = useState<string>()
  const [retryingLlm, setRetryingLlm] = useState(false)
  const load = async () => {
    try {
      const [runData, actionData, conversationData, operationData] = await Promise.all([
        api<{ items: Run[] }>('/automation/runs'),
        api<{ items: Action[] }>('/automation/actions'),
        api<{ items: Conversation[] }>('/conversations'),
        api<Operations>('/automation/operations/status'),
      ])
      setRuns(runData.items); setActions(actionData.items); setConversations(conversationData.items)
      setOperations(operationData); setLoadError(undefined)
    } catch (error) {
      setRuns([]); setActions([]); setConversations([]); setOperations(undefined)
      setLoadError(error instanceof Error ? error.message : '无法连接 API 服务')
    }
  }
  const reconnect = async (run: Run) => {
    setReconnectingRunId(run.id)
    try {
      await api(`/automation/runs/${run.id}/resume`, { method: 'POST' })
      message.success(`${run.platform} 已恢复，正在重新检查页面`)
      await load()
    } catch (error) {
      message.error(error instanceof Error ? error.message : '重新连接失败，请确认平台页面已经打开')
    } finally {
      setReconnectingRunId(undefined)
    }
  }
  const retryLlm = async () => {
    setRetryingLlm(true)
    try {
      const circuit = await api<Operations['llm_circuit']>(
        '/automation/llm-circuit/retry',
        { method: 'POST' },
      )
      if (circuit.status === 'CLOSED') message.success('LLM 已恢复，Agent 将自动继续工作')
      else message.error('LLM 仍不可用，系统会按计划继续重试')
      await load()
    } catch (error) {
      message.error(error instanceof Error ? error.message : 'LLM 重试失败')
    } finally {
      setRetryingLlm(false)
    }
  }
  useEffect(() => {
    void Promise.all([
      api<{ items: Run[] }>('/automation/runs'),
      api<{ items: Action[] }>('/automation/actions'),
      api<{ items: Conversation[] }>('/conversations'),
      api<Operations>('/automation/operations/status'),
    ]).then(([runData, actionData, conversationData, operationData]) => {
      setRuns(runData.items); setActions(actionData.items); setConversations(conversationData.items)
      setOperations(operationData); setLoadError(undefined)
    }).catch((error: unknown) => {
      setRuns([]); setActions([]); setConversations([]); setOperations(undefined)
      setLoadError(error instanceof Error ? error.message : '无法连接 API 服务')
    })
  }, [])
  const currentRuns = activeRuns(runs)
  const processedCount = currentRuns.reduce((sum, item) => sum + item.processed_count, 0)
  const currentWorkers = activeWorkers(operations?.workers)
  const workerRunning = currentWorkers.length > 0
  const llmCircuit = operations?.llm_circuit
  return <Space direction="vertical" size="large" style={{ width: '100%' }}>
    {loadError && <Alert type="error" showIcon message="服务不可用"
      description={`无法获取实时运行状态：${loadError}`} />}
    {llmCircuit && llmCircuit.status !== 'CLOSED' && <Alert type="error" showIcon
      message="LLM 暂不可用，Agent 业务已暂停"
      description={<Space direction="vertical">
        <span>原因：{llmCircuit.failure_code ?? '正在探测'}；已探测 {llmCircuit.probe_attempt_count} 次
          {llmCircuit.next_probe_at
            ? `；下次自动重试：${new Date(llmCircuit.next_probe_at).toLocaleString('zh-CN')}` : ''}</span>
        <Button danger loading={retryingLlm} onClick={() => void retryLlm()}>
          重新加载配置并重试 LLM
        </Button>
      </Space>} />}
    {(!operations?.database_ready || !operations?.llm_configured) &&
      <Alert type="error" showIcon message="Agent 当前存在阻断项"
        description="请在系统设置中检查数据库、LLM 和平台会话。" />}
    <Row gutter={[16, 16]}>
      <Col xs={24} sm={12} xl={6}><Card><Statistic title="Agent 状态"
        value={loadError ? '服务不可用' : agentStatusText(runs, workerRunning)} /></Card></Col>
      <Col xs={24} sm={12} xl={6}><Card><Statistic title="已处理职位/消息"
        value={processedCount} /></Card></Col>
      <Col xs={24} sm={12} xl={6}><Card><Statistic title="自动动作"
        value={actions.filter((item) => item.status === 'SUCCEEDED').length} /></Card></Col>
      <Col xs={24} sm={12} xl={6}><Card><Statistic title="待确认面试"
        value={operations?.pending_confirmation_count ?? 0} /></Card></Col>
    </Row>
    <Card title="运行状态" extra={<Button onClick={() => void load()}>刷新</Button>}>
      <Descriptions column={{ xs: 1, md: 2 }} items={[
        { key: 'run', label: '运行', children: currentRuns.length > 0
          ? <Space size={[4, 4]} wrap>{currentRuns.map((run) =>
            <Space key={run.id} size={4}>
              <Tag color={statusColor(run.status)}>
                {run.platform}:{runStatusText(run, workerRunning)}
                {run.status === 'PAUSED' && run.pause_reason_codes.length > 0
                  ? `（${run.pause_reason_codes.join('、')}）` : ''}
              </Tag>
              {canReconnectRun(run) && <Button size="small" loading={reconnectingRunId === run.id}
                onClick={() => void reconnect(run)}>重新连接</Button>}
            </Space>)}</Space>
          : <Tag>STOPPED</Tag> },
        { key: 'platform', label: '平台', children:
          currentRuns.map((item) => item.platform).join('、') || '-' },
        { key: 'worker', label: 'Worker', children: <Tag color={
          currentWorkers.length > 1 ? 'red'
            : currentWorkers[0]?.status === 'STALE' ? 'orange'
              : currentWorkers.length === 1 ? 'green' : 'default'
        }>{workerStatusText(operations?.workers)}</Tag> },
        { key: 'unknown', label: '未知结果', children: operations?.unknown_action_count ?? 0 },
        { key: 'discrepancies', label: '审计差异', children: operations?.discrepancies.length ?? 0 },
        { key: 'conversations', label: '活跃会话', children:
          conversations.filter((item) => !['ENDED', 'DECLINED'].includes(item.state)).length },
      ]} />
    </Card>
  </Space>
}
