import { Layout, Tabs, Typography } from 'antd'
import { JobPage } from './pages/JobPage'
import { ConversationPage } from './pages/ConversationPage'
import { BrowserPage } from './pages/BrowserPage'
import { ProfilePage } from './pages/ProfilePage'
import { StrategyPage } from './pages/StrategyPage'

export default function App() {
  return <Layout style={{ minHeight: '100vh' }}><Layout.Header><Typography.Title level={3} style={{ color: 'white', margin: 16 }}>半自动求职 Agent</Typography.Title></Layout.Header>
    <Layout.Content style={{ padding: 24 }}><Tabs items={[
      { key: 'profile', label: '候选人资料', children: <ProfilePage /> },
      { key: 'strategy', label: '求职策略', children: <StrategyPage /> },
      { key: 'jobs', label: '模拟 JD 与职位', children: <JobPage /> },
      { key: 'conversation', label: '知识库与模拟沟通', children: <ConversationPage /> },
      { key: 'browser', label: '招聘网站只读', children: <BrowserPage /> },
    ]} /></Layout.Content></Layout>
}
