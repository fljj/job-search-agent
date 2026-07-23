import {
  Button, Card, Form, Input, InputNumber, Select, Space, Switch, Table, Tabs, Tag, message,
} from 'antd'
import { useEffect, useState } from 'react'
import { api } from '../api/client'

interface Profile {
  version: number; name: string; total_years: number; management_years: number
  has_architecture_experience: boolean; has_core_system_experience: boolean
  skills: unknown[]; industry_experiences: unknown[]
}
interface KnowledgeItem {
  id: string; category: string; key: string; fact: string; source: string
  allowed_for_auto_reply: boolean; sensitivity: string
}
interface Resume {
  id: string; attachment_name: string; platform: string; target_directions: string[]
  is_available: boolean
}

export function ProfilePage() {
  const [profileForm] = Form.useForm()
  const [knowledgeForm] = Form.useForm()
  const [resumeForm] = Form.useForm()
  const [version, setVersion] = useState<number>()
  const [knowledge, setKnowledge] = useState<KnowledgeItem[]>([])
  const [resumes, setResumes] = useState<Resume[]>([])
  const loadRelated = async () => {
    const [facts, attachments] = await Promise.all([
      api<{ items: KnowledgeItem[] }>('/knowledge-items'),
      api<{ items: Resume[] }>('/resumes'),
    ])
    setKnowledge(facts.items); setResumes(attachments.items)
  }
  useEffect(() => {
    void Promise.all([
      api<Profile>('/profile').catch(() => undefined),
      api<{ items: KnowledgeItem[] }>('/knowledge-items'),
      api<{ items: Resume[] }>('/resumes'),
    ]).then(([profile, facts, attachments]) => {
      if (profile) {
        setVersion(profile.version)
        profileForm.setFieldsValue({
          ...profile,
          skills_json: JSON.stringify(profile.skills, null, 2),
          industries_json: JSON.stringify(profile.industry_experiences, null, 2),
        })
      }
      setKnowledge(facts.items)
      setResumes(attachments.items)
    })
  }, [profileForm])
  const saveProfile = async (values: Record<string, unknown>) => {
    try {
      const saved = await api<Profile>('/profile', {
        method: 'PUT',
        body: JSON.stringify({
          ...values,
          version,
          skills: JSON.parse(values.skills_json as string),
          industry_experiences: JSON.parse(values.industries_json as string),
        }),
      })
      setVersion(saved.version); message.success('候选人资料已保存')
    } catch (error) {
      message.error(error instanceof Error ? error.message : '资料格式无效')
    }
  }
  const saveKnowledge = async (values: Record<string, unknown>) => {
    await api('/knowledge-items', {
      method: 'POST',
      body: JSON.stringify({ ...values, verified_at: new Date().toISOString() }),
    })
    knowledgeForm.resetFields(); await loadRelated(); message.success('可信事实已保存')
  }
  const saveResume = async (values: Record<string, unknown>) => {
    await api('/resumes', {
      method: 'POST',
      body: JSON.stringify({
        ...values,
        target_directions: String(values.target_directions).split(/[,，]/)
          .map((item) => item.trim()).filter(Boolean),
      }),
    })
    resumeForm.resetFields(); await loadRelated(); message.success('网站附件简历已登记')
  }
  return <Tabs items={[
    {
      key: 'profile', label: '基础资料与评分事实', children:
        <Card><Form form={profileForm} layout="vertical" onFinish={(value) => void saveProfile(value)}
          initialValues={{ total_years: 0, management_years: 0,
            has_architecture_experience: false, has_core_system_experience: false,
            skills_json: '[]', industries_json: '[]' }}>
          <Form.Item name="name" label="姓名" rules={[{ required: true }]}><Input /></Form.Item>
          <Space wrap>
            <Form.Item name="total_years" label="总工作年限"><InputNumber min={0} /></Form.Item>
            <Form.Item name="management_years" label="管理年限"><InputNumber min={0} /></Form.Item>
            <Form.Item name="has_architecture_experience" label="架构经验"
              valuePropName="checked"><Switch /></Form.Item>
            <Form.Item name="has_core_system_experience" label="核心系统经验"
              valuePropName="checked"><Switch /></Form.Item>
          </Space>
          <Form.Item name="skills_json" label="技能及证据（结构化数据）"
            rules={[{ required: true }]}><Input.TextArea rows={8} /></Form.Item>
          <Form.Item name="industries_json" label="行业经验（结构化数据）"
            rules={[{ required: true }]}><Input.TextArea rows={5} /></Form.Item>
          <Button type="primary" htmlType="submit">保存候选人资料</Button>
        </Form></Card>,
    },
    {
      key: 'knowledge', label: '自动回复知识库', children: <Space direction="vertical"
        style={{ width: '100%' }} size="large">
        <Card title="新增可信事实"><Form form={knowledgeForm} layout="vertical"
          onFinish={(value) => void saveKnowledge(value)}
          initialValues={{ sensitivity: 'NORMAL', allowed_for_auto_reply: true, source: '用户确认' }}>
          <Space wrap>
            <Form.Item name="category" label="分类" rules={[{ required: true }]}><Input /></Form.Item>
            <Form.Item name="key" label="事实键" rules={[{ required: true }]}><Input /></Form.Item>
            <Form.Item name="source" label="来源" rules={[{ required: true }]}><Input /></Form.Item>
            <Form.Item name="sensitivity" label="敏感度"><Select style={{ width: 140 }} options={
              ['NORMAL', 'SENSITIVE', 'PROHIBITED'].map((value) => ({ value }))} /></Form.Item>
            <Form.Item name="allowed_for_auto_reply" label="允许自动引用"
              valuePropName="checked"><Switch /></Form.Item>
          </Space>
          <Form.Item name="fact" label="已验证事实" rules={[{ required: true }]}>
            <Input.TextArea rows={4} /></Form.Item>
          <Button type="primary" htmlType="submit">保存可信事实</Button>
        </Form></Card>
        <Card title="已有知识项"><Table rowKey="id" dataSource={knowledge} columns={[
          { title: '分类', dataIndex: 'category' }, { title: '键', dataIndex: 'key' },
          { title: '事实', dataIndex: 'fact' }, { title: '来源', dataIndex: 'source' },
          { title: '权限', render: (_: unknown, item: KnowledgeItem) =>
            <Tag color={item.allowed_for_auto_reply ? 'green' : 'orange'}>
              {item.allowed_for_auto_reply ? '允许自动引用' : '禁止自动引用'}</Tag> },
        ]} /></Card>
      </Space>,
    },
    {
      key: 'resumes', label: '网站附件简历', children: <Space direction="vertical"
        style={{ width: '100%' }} size="large">
        <Card title="登记已上传附件"><Form form={resumeForm} layout="vertical"
          onFinish={(value) => void saveResume(value)}
          initialValues={{ platform: 'BOSS', is_available: true }}>
          <Space wrap>
            <Form.Item name="platform" label="平台"><Select style={{ width: 140 }}
              options={[{ value: 'BOSS' }, { value: 'MAIMAI' }, { value: 'MOCK' }]} /></Form.Item>
            <Form.Item name="attachment_name" label="平台内附件名称"
              rules={[{ required: true }]}><Input /></Form.Item>
            <Form.Item name="target_directions" label="适用岗位方向（逗号分隔）"
              rules={[{ required: true }]}><Input /></Form.Item>
            <Form.Item name="is_available" label="可用" valuePropName="checked"><Switch /></Form.Item>
          </Space>
          <Button type="primary" htmlType="submit">登记附件简历</Button>
        </Form></Card>
        <Card title="可用附件"><Table rowKey="id" dataSource={resumes} columns={[
          { title: '附件名', dataIndex: 'attachment_name' }, { title: '平台', dataIndex: 'platform' },
          { title: '适用方向', dataIndex: 'target_directions',
            render: (items: string[]) => items.join('、') },
          { title: '状态', dataIndex: 'is_available',
            render: (value: boolean) => <Tag color={value ? 'green' : 'default'}>
              {value ? '可用' : '停用'}</Tag> },
        ]} /></Card>
      </Space>,
    },
  ]} />
}
