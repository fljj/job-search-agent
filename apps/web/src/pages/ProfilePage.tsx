import { Button, Card, Form, Input, InputNumber, Switch, message } from 'antd'
import { useEffect, useState } from 'react'
import { api } from '../api/client'

interface Profile {
  id: string
  version: number
  name: string
  total_years: number
  management_years: number
  has_architecture_experience: boolean
  has_core_system_experience: boolean
  skills: unknown[]
  industry_experiences: unknown[]
}

export function ProfilePage() {
  const [form] = Form.useForm()
  const [version, setVersion] = useState<number | undefined>()
  useEffect(() => {
    api<Profile>('/profile').then((profile) => {
      setVersion(profile.version)
      form.setFieldsValue({ ...profile, skills_json: JSON.stringify(profile.skills, null, 2),
        industries_json: JSON.stringify(profile.industry_experiences, null, 2) })
    }).catch(() => { /* 尚未创建资料时保持空表单 */ })
  }, [form])
  const save = async (values: Record<string, unknown>) => {
    const saved = await api<Profile>('/profile', { method: 'PUT', body: JSON.stringify({
      ...values, version, skills: JSON.parse(values.skills_json as string),
      industry_experiences: JSON.parse(values.industries_json as string),
    }) })
    setVersion(saved.version)
    message.success('候选人资料已保存')
  }
  return <Card title="候选人评分资料"><Form form={form} layout="vertical" onFinish={save}
    initialValues={{ total_years: 0, management_years: 0, has_architecture_experience: false,
      has_core_system_experience: false, skills_json: '[]', industries_json: '[]' }}>
    <Form.Item name="name" label="姓名" rules={[{ required: true }]}><Input /></Form.Item>
    <Form.Item name="total_years" label="总工作年限"><InputNumber min={0} /></Form.Item>
    <Form.Item name="management_years" label="管理年限"><InputNumber min={0} /></Form.Item>
    <Form.Item name="has_architecture_experience" label="架构经验" valuePropName="checked"><Switch /></Form.Item>
    <Form.Item name="has_core_system_experience" label="核心系统经验" valuePropName="checked"><Switch /></Form.Item>
    <Form.Item name="skills_json" label="技能 JSON" rules={[{ required: true }]}><Input.TextArea rows={8} /></Form.Item>
    <Form.Item name="industries_json" label="行业经验 JSON" rules={[{ required: true }]}><Input.TextArea rows={5} /></Form.Item>
    <Button type="primary" htmlType="submit">保存</Button>
  </Form></Card>
}
