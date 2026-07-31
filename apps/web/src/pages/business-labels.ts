const labels: Record<string, string> = {
  RUNNING: '运行中', PAUSED: '已暂停', STOPPED: '已停止', STALE: '心跳过期',
  SESSION_READY: '页面就绪', SESSION_UNAVAILABLE: '页面不可用',
  OPEN: '模型暂停', PROBING: '模型探测中', CLOSED: '模型正常',
  GREETING: '主动招呼', REPLY: '普通回复', RESUME: '发送默认简历',
  MISMATCH_DECLINE: '礼貌拒绝', SUCCEEDED: '已成功', APPROVED: '待执行',
  FAILED_RETRYABLE: '可重试失败', FAILED_FINAL: '最终失败',
  OUTCOME_UNKNOWN: '结果待核对', ELIGIBLE: '符合评分条件',
  MISMATCH: '明确不匹配', ROUGH_MATCH: '大致匹配', FULL_MATCH: '完全匹配',
  UNKNOWN: '信息不足',
}

export const businessLabel = (code?: string) => {
  if (!code) return '-'
  return labels[code] ?? `未知状态（${code}）`
}
