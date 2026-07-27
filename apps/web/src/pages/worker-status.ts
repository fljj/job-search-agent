export interface WorkerStatus {
  worker_id: string
  status: string
}

export function activeWorkers(workers: WorkerStatus[] = []) {
  return workers.filter((worker) => worker.status === 'RUNNING')
}

export function workerStatusText(workers: WorkerStatus[] = []) {
  const active = activeWorkers(workers)
  if (active.length === 0) return '未运行'
  const details = active.map((worker) => `${worker.worker_id}:${worker.status}`).join('、')
  return active.length > 1 ? `异常：存在 ${active.length} 个活动 Worker（${details}）` : details
}
