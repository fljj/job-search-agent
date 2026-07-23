import { lazy, Suspense, useState } from 'react'
import { Layout, Spin, Tabs, Typography } from 'antd'

const pages = {
  profile: lazy(() => import('./pages/ProfilePage').then(({ ProfilePage }) => ({ default: ProfilePage }))),
  strategy: lazy(() => import('./pages/StrategyPage').then(({ StrategyPage }) => ({ default: StrategyPage }))),
  jobs: lazy(() => import('./pages/JobPage').then(({ JobPage }) => ({ default: JobPage }))),
  conversation: lazy(() => import('./pages/ConversationPage').then(({ ConversationPage }) => ({ default: ConversationPage }))),
  browser: lazy(() => import('./pages/BrowserPage').then(({ BrowserPage }) => ({ default: BrowserPage }))),
  actions: lazy(() => import('./pages/ActionPage').then(({ ActionPage }) => ({ default: ActionPage }))),
  automation: lazy(() => import('./pages/AutomationPage').then(({ AutomationPage }) => ({ default: AutomationPage }))),
  scheduling: lazy(() => import('./pages/SchedulingPage').then(({ SchedulingPage }) => ({ default: SchedulingPage }))),
}

type PageKey = keyof typeof pages

const labels: Record<PageKey, string> = {
  profile: '候选人资料',
  strategy: '求职策略',
  jobs: '模拟 JD 与职位',
  conversation: '知识库与模拟沟通',
  browser: '招聘网站读取',
  actions: '普通人工确认',
  automation: 'Agent 运行控制台',
  scheduling: '电话与面试确认',
}

export default function App() {
  const [activeKey, setActiveKey] = useState<PageKey>('profile')
  const ActivePage = pages[activeKey]
  return <Layout style={{ minHeight: '100vh' }}>
    <Layout.Header>
      <Typography.Title level={3} style={{ color: 'white', margin: 16 }}>半自动求职 Agent</Typography.Title>
    </Layout.Header>
    <Layout.Content style={{ padding: 24 }}>
      <Tabs activeKey={activeKey} onChange={(key) => setActiveKey(key as PageKey)}
        items={(Object.keys(labels) as PageKey[]).map((key) => ({ key, label: labels[key] }))} />
      <Suspense fallback={<Spin tip="页面加载中" />}>
        <ActivePage />
      </Suspense>
    </Layout.Content>
  </Layout>
}
