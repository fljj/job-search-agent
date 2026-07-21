import { Alert, Button, Card, Form, Input, Select, Space, Table, Tag, message } from 'antd'
import { useEffect, useState } from 'react'
import { api } from '../api/client'

interface Session { id: string; platform: string; status: string; last_reason_codes: string[] }

export function BrowserPage() {
  const [sessions, setSessions] = useState<Session[]>([])
  const [result, setResult] = useState<Record<string, unknown>>()
  const load = () => api<{ items: Session[] }>('/browser/sessions').then((data) => setSessions(data.items))
  useEffect(() => { api<{ items: Session[] }>('/browser/sessions').then((data) => setSessions(data.items)) }, [])
  const read = async (values: Record<string, string>) => {
    const data = await api<Record<string, unknown>>('/browser/read-current', {
      method: 'POST', body: JSON.stringify(values),
    })
    setResult(data); await load(); message.success('当前页面只读检查完成')
  }
  return <Space direction="vertical" style={{ width: '100%' }}>
    <Alert type="warning" showIcon message="仅读模式" description="请先由用户手动登录并打开目标页。系统只读取当前标签页，不点击、不输入、不发送、不处理验证码。" />
    <Card title="读取当前招聘页面"><Form layout="vertical" onFinish={read}
      initialValues={{ platform: 'BOSS', cdp_url: 'http://127.0.0.1:9222' }}>
      <Form.Item name="platform" label="平台"><Select options={[{ value: 'BOSS' }, { value: 'MAIMAI' }]} /></Form.Item>
      <Form.Item name="cdp_url" label="本机 CDP 地址"><Input /></Form.Item>
      <Form.Item name="job_id" label="对话所属职位 ID（只读对话页时必填）"><Input /></Form.Item>
      <Form.Item name="expected_company" label="预期公司（可选复核）"><Input /></Form.Item>
      <Form.Item name="expected_job_title" label="预期职位（可选复核）"><Input /></Form.Item>
      <Form.Item name="expected_recruiter" label="预期招聘人（可选复核）"><Input /></Form.Item>
      <Button type="primary" htmlType="submit">只读读取</Button>
    </Form>{result && <pre style={{ whiteSpace: 'pre-wrap' }}>{JSON.stringify(result, null, 2)}</pre>}</Card>
    <Card title="平台会话状态"><Table rowKey="id" dataSource={sessions} pagination={false} columns={[
      { title: '平台', dataIndex: 'platform' },
      { title: '状态', dataIndex: 'status', render: (value: string) => <Tag>{value}</Tag> },
      { title: '原因', dataIndex: 'last_reason_codes', render: (items: string[]) => items.join('、') || '-' },
    ]} /></Card>
  </Space>
}
