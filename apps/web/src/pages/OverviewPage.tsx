import { Alert, Button, Card, Col, Descriptions, Row, Space, Statistic, Tag } from 'antd'
import { useEffect, useState } from 'react'
import { api } from '../api/client'
import { statusColor } from './automation-status'

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
}
interface Rollout {
  status: string; current_level: number; level_name: string; remaining_hours: number
  safety_metrics: Record<string, number>
}

export function OverviewPage() {
  const [runs, setRuns] = useState<Run[]>([])
  const [actions, setActions] = useState<Action[]>([])
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [operations, setOperations] = useState<Operations>()
  const [rollout, setRollout] = useState<Rollout>()
  const load = async () => {
    const [runData, actionData, conversationData, operationData, rolloutData] = await Promise.all([
      api<{ items: Run[] }>('/automation/runs'),
      api<{ items: Action[] }>('/automation/actions'),
      api<{ items: Conversation[] }>('/conversations'),
      api<Operations>('/automation/operations/status'),
      api<{ items: Rollout[] }>('/automation/rollouts'),
    ])
    setRuns(runData.items); setActions(actionData.items); setConversations(conversationData.items)
    setOperations(operationData); setRollout(rolloutData.items[0])
  }
  useEffect(() => {
    void Promise.all([
      api<{ items: Run[] }>('/automation/runs'),
      api<{ items: Action[] }>('/automation/actions'),
      api<{ items: Conversation[] }>('/conversations'),
      api<Operations>('/automation/operations/status'),
      api<{ items: Rollout[] }>('/automation/rollouts'),
    ]).then(([runData, actionData, conversationData, operationData, rolloutData]) => {
      setRuns(runData.items); setActions(actionData.items); setConversations(conversationData.items)
      setOperations(operationData); setRollout(rolloutData.items[0])
    })
  }, [])
  const activeRun = runs.find((item) => ['RUNNING', 'PAUSED'].includes(item.status))
  const safetyErrors = Object.values(rollout?.safety_metrics ?? {}).reduce((sum, value) => sum + value, 0)
  return <Space direction="vertical" size="large" style={{ width: '100%' }}>
    {(!operations?.database_ready || !operations?.llm_configured || safetyErrors > 0) &&
      <Alert type="error" showIcon message="Agent 当前存在阻断项"
        description="请在系统设置中检查数据库、LLM、灰度安全指标和平台会话。" />}
    <Row gutter={[16, 16]}>
      <Col xs={24} sm={12} xl={6}><Card><Statistic title="Agent 状态"
        value={activeRun?.status ?? '未启动'} /></Card></Col>
      <Col xs={24} sm={12} xl={6}><Card><Statistic title="已处理职位/消息"
        value={activeRun?.processed_count ?? 0} /></Card></Col>
      <Col xs={24} sm={12} xl={6}><Card><Statistic title="自动动作"
        value={actions.filter((item) => item.status === 'SUCCEEDED').length} /></Card></Col>
      <Col xs={24} sm={12} xl={6}><Card><Statistic title="待确认面试"
        value={operations?.pending_confirmation_count ?? 0} /></Card></Col>
    </Row>
    <Card title="运行状态" extra={<Button onClick={() => void load()}>刷新</Button>}>
      <Descriptions column={{ xs: 1, md: 2 }} items={[
        { key: 'run', label: '运行', children: <Tag color={statusColor(activeRun?.status ?? '')}>
          {activeRun?.status ?? 'STOPPED'}</Tag> },
        { key: 'platform', label: '平台', children: activeRun?.platform ?? '-' },
        { key: 'worker', label: 'Worker', children:
          operations?.workers.map((item) => `${item.worker_id}:${item.status}`).join('、') || '无' },
        { key: 'rollout', label: 'BOSS 灰度', children: rollout
          ? `${rollout.current_level} - ${rollout.level_name}（${rollout.status}）` : '未初始化' },
        { key: 'unknown', label: '未知结果', children: operations?.unknown_action_count ?? 0 },
        { key: 'discrepancies', label: '审计差异', children: operations?.discrepancies.length ?? 0 },
        { key: 'safety', label: '安全错误', children: safetyErrors },
        { key: 'conversations', label: '活跃会话', children:
          conversations.filter((item) => !['ENDED', 'DECLINED'].includes(item.state)).length },
      ]} />
    </Card>
  </Space>
}
