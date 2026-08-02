import { describe, expect, it } from 'vitest'
import { businessLabel } from './business-labels'

describe('businessLabel', () => {
  it('将职位中心常见原因码转换为可理解的中文', () => {
    expect(businessLabel('JOB_ALREADY_HAS_CONVERSATION')).toBe(
      '已有沟通会话，无需再次打招呼',
    )
    expect(businessLabel('LLM_DOES_NOT_RECOMMEND_CONTACT')).toBe(
      'AI 判断暂不建议主动沟通',
    )
    expect(businessLabel('HEADHUNTER_PROACTIVE_CONTACT_BLOCKED')).toBe(
      '猎头发布，按策略不主动打招呼',
    )
  })

  it('保留未知新状态的原始码便于排查', () => {
    expect(businessLabel('NEW_REASON_CODE')).toBe('未知状态（NEW_REASON_CODE）')
  })
})
