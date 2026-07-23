export const statusColor = (status: string) => ['RUNNING', 'ACTIVE', 'SUCCEEDED'].includes(status)
  ? 'green' : status === 'PAUSED' || status === 'OUTCOME_UNKNOWN' ? 'orange' : 'default'
