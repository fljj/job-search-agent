import { Alert, Button, Card, Form, Input, InputNumber, Select, Space, Switch, message } from 'antd'
import { api } from '../api/client'

interface SettingForm {
  scope_type: 'GLOBAL' | 'PLATFORM' | 'STRATEGY'; scope_key: string
  enabled: boolean; paused: boolean; auto_greet_enabled: boolean
  auto_greet_min_score: number; auto_reply_enabled: boolean
  auto_reply_min_confidence: number; auto_resume_enabled: boolean
  auto_resume_min_score: number; hourly_limit: number; daily_limit: number
}

export function AutomationPage() {
  const [form] = Form.useForm<SettingForm>()
  const save = async (values: SettingForm) => {
    await api('/automation/settings', { method: 'PUT', body: JSON.stringify(values) })
    message.success('自动化配置已保存')
  }
  return <Space direction="vertical" style={{ width: '100%' }}>
    <Alert type="warning" showIcon message="自动化默认关闭"
      description="全局、平台和策略配置只会逐层收紧权限。敏感问题、具体时间、硬性排除职位和页面身份异常不能通过配置绕过。" />
    <Card title="自动化范围配置"><Form form={form} layout="vertical" onFinish={(value) => void save(value)}
      initialValues={{ scope_type: 'GLOBAL', scope_key: 'GLOBAL', enabled: false, paused: false,
        auto_greet_enabled: false, auto_greet_min_score: 80, auto_reply_enabled: false,
        auto_reply_min_confidence: .9, auto_resume_enabled: false, auto_resume_min_score: 60,
        hourly_limit: 10, daily_limit: 50 }}>
      <Form.Item name="scope_type" label="范围"><Select options={[
        { value: 'GLOBAL', label: '全局' }, { value: 'PLATFORM', label: '平台' },
        { value: 'STRATEGY', label: '策略' },
      ]} /></Form.Item>
      <Form.Item name="scope_key" label="范围标识" rules={[{ required: true }]}><Input placeholder="GLOBAL、BOSS、MAIMAI 或策略 ID" /></Form.Item>
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
