export const statusColor = (status: string) => status === 'RUNNING' || status === 'SUCCEEDED'
  ? 'green' : status === 'PAUSED' || status === 'OUTCOME_UNKNOWN' ? 'orange' : 'default'
