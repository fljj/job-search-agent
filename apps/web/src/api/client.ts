const API_BASE = import.meta.env.VITE_API_BASE ?? 'http://localhost:8000/api/v1'

export async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...options?.headers },
  })
  const body = await response.json()
  if (!response.ok) throw new Error(body.error?.message ?? '请求失败')
  return body.data as T
}
