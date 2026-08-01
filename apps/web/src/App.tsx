import { lazy, Suspense, useEffect, useState } from 'react'
import { Layout, Menu, Spin, Typography } from 'antd'

const pages = {
  overview: lazy(() => import('./pages/OverviewPage').then(({ OverviewPage }) => ({ default: OverviewPage }))),
  jobs: lazy(() => import('./pages/JobPage').then(({ JobPage }) => ({ default: JobPage }))),
  messages: lazy(() => import('./pages/MessagePage').then(({ MessagePage }) => ({ default: MessagePage }))),
  confirmations: lazy(() => import('./pages/HumanConfirmationPage').then(({ HumanConfirmationPage }) => ({ default: HumanConfirmationPage }))),
  scheduling: lazy(() => import('./pages/SchedulingPage').then(({ SchedulingPage }) => ({ default: SchedulingPage }))),
  strategy: lazy(() => import('./pages/StrategyPage').then(({ StrategyPage }) => ({ default: StrategyPage }))),
  profile: lazy(() => import('./pages/ProfilePage').then(({ ProfilePage }) => ({ default: ProfilePage }))),
  settings: lazy(() => import('./pages/AutomationPage').then(({ AutomationPage }) => ({ default: AutomationPage }))),
}

type PageKey = keyof typeof pages

function pageFromHash(): PageKey {
  const key = window.location.hash.slice(1).split('?')[0]
  return key in pages ? key as PageKey : 'overview'
}

const labels: Record<PageKey, string> = {
  overview: '总览',
  jobs: '职位中心',
  messages: '消息中心',
  confirmations: '人工确认',
  scheduling: '面试确认',
  strategy: '求职策略',
  profile: '候选人中心',
  settings: '系统设置',
}

export default function App() {
  const [activeKey, setActiveKey] = useState<PageKey>(pageFromHash)
  const [locationKey, setLocationKey] = useState(window.location.hash)
  useEffect(() => {
    const syncPage = () => {
      setActiveKey(pageFromHash())
      setLocationKey(window.location.hash)
    }
    window.addEventListener('hashchange', syncPage)
    return () => window.removeEventListener('hashchange', syncPage)
  }, [])
  const ActivePage = pages[activeKey]
  return <Layout style={{ minHeight: '100vh' }}>
    <Layout.Sider width={220} breakpoint="lg" collapsedWidth={0}>
      <Typography.Title level={4} style={{ color: 'white', margin: 20 }}>
        无人值守求职 Agent
      </Typography.Title>
      <Menu theme="dark" mode="inline" selectedKeys={[activeKey]}
        onClick={({ key }) => {
          window.location.hash = key
          setActiveKey(key as PageKey)
        }}
        items={(Object.keys(labels) as PageKey[]).map((key) => ({ key, label: labels[key] }))} />
    </Layout.Sider>
    <Layout>
      <Layout.Header style={{ background: '#fff', padding: '0 24px' }}>
        <Typography.Title level={3} style={{ margin: '16px 0' }}>{labels[activeKey]}</Typography.Title>
      </Layout.Header>
      <Layout.Content style={{ padding: 24 }}>
        <Suspense fallback={<Spin tip="页面加载中" />}>
          <ActivePage key={locationKey} />
        </Suspense>
      </Layout.Content>
    </Layout>
  </Layout>
}
