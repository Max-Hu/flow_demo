import type {
  FlowContent,
  FlowDetail,
  FlowCredential,
  FlowSummary,
  FlowSchedule,
  FlowVersion,
  JsonSchema,
  NodeTypeDefinition,
  RunDetail,
  RunSummary,
  ValidationResult,
} from './types'

export const API_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:8000/api'
let csrfToken = ''

export function setCsrfToken(value: string) {
  csrfToken = value
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    ...options,
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      ...(csrfToken && options?.method && !['GET', 'HEAD'].includes(options.method)
        ? { 'X-CSRF-Token': csrfToken }
        : {}),
      ...options?.headers,
    },
  })
  if (response.status === 401 && path !== '/auth/login') {
    csrfToken = ''
    window.dispatchEvent(new Event('flowforge:unauthorized'))
  }
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: response.statusText }))
    const detail = typeof body.detail === 'string'
      ? body.detail
      : body.detail?.message ?? JSON.stringify(body.detail)
    throw new Error(detail || `Request failed with status ${response.status}`)
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export const api = {
  login: async (username: string, password: string) => {
    const result = await request<{ username: string; csrf_token: string }>('/auth/login', {
      method: 'POST', body: JSON.stringify({ username, password }),
    })
    setCsrfToken(result.csrf_token)
    return result
  },
  me: async () => {
    const result = await request<{ username: string; csrf_token: string }>('/auth/me')
    setCsrfToken(result.csrf_token)
    return result
  },
  logout: async () => {
    await request<void>('/auth/logout', { method: 'POST' })
    setCsrfToken('')
  },
  nodeTypes: () => request<NodeTypeDefinition[]>('/node-types'),
  flows: () => request<FlowSummary[]>('/flows'),
  flow: (id: string) => request<FlowDetail>(`/flows/${id}`),
  createFlow: (name: string, content: FlowContent, inputSchema?: JsonSchema) => request<FlowDetail>('/flows', {
    method: 'POST',
    body: JSON.stringify({ name, description: 'A visual automation workflow.', content, inputSchema }),
  }),
  saveDraft: (
    id: string,
    content: FlowContent,
    inputSchema: JsonSchema,
    configSchema: JsonSchema,
    defaultConfig: Record<string, unknown>,
    rowVersion: number,
  ) => request<FlowDetail>(
    `/flows/${id}/draft`,
    {
      method: 'PUT',
      body: JSON.stringify({ content, inputSchema, configSchema, defaultConfig, expectedRowVersion: rowVersion }),
    },
  ),
  validate: (id: string) => request<ValidationResult>(`/flows/${id}/validate`, { method: 'POST' }),
  publish: (id: string) => request<{ version_number: number }>(`/flows/${id}/publish`, {
    method: 'POST',
  }),
  versions: (id: string) => request<FlowVersion[]>(`/flows/${id}/versions`),
  rollbackVersion: (id: string, versionNumber: number) => request<FlowVersion>(
    `/flows/${id}/versions/${versionNumber}/rollback`,
    { method: 'POST' },
  ),
  updateFlowStatus: (id: string, status: 'ACTIVE' | 'PAUSED' | 'ARCHIVED') => request<FlowDetail>(
    `/flows/${id}/status`,
    { method: 'PATCH', body: JSON.stringify({ status }) },
  ),
  schedules: (id: string) => request<FlowSchedule[]>(`/flows/${id}/schedules`),
  credentials: (id: string) => request<FlowCredential[]>(`/flows/${id}/credentials`),
  createCredential: (id: string, payload: {
    alias: string; type: string; allowedOrigins: string[]; secret: Record<string, unknown>
  }) => request<FlowCredential>(`/flows/${id}/credentials`, {
    method: 'POST', body: JSON.stringify(payload),
  }),
  rotateCredential: (id: string, credentialId: string, secret: Record<string, unknown>) => request<FlowCredential>(
    `/flows/${id}/credentials/${credentialId}/rotate`,
    { method: 'POST', body: JSON.stringify({ secret }) },
  ),
  updateCredential: (id: string, credentialId: string, payload: { enabled?: boolean; allowedOrigins?: string[] }) => request<FlowCredential>(
    `/flows/${id}/credentials/${credentialId}`,
    { method: 'PATCH', body: JSON.stringify(payload) },
  ),
  deleteCredential: (id: string, credentialId: string) => request<void>(
    `/flows/${id}/credentials/${credentialId}`, { method: 'DELETE' },
  ),
  createSchedule: (
    id: string,
    payload: { name: string; cronExpression: string; timezone: string; versionNumber: number; inputData: Record<string, unknown>; configOverrides: Record<string, unknown>; enabled: boolean },
  ) => request<FlowSchedule>(`/flows/${id}/schedules`, {
    method: 'POST', body: JSON.stringify(payload),
  }),
  updateSchedule: (id: string, scheduleId: string, payload: Partial<{
    name: string; cronExpression: string; timezone: string; versionNumber: number; inputData: Record<string, unknown>; configOverrides: Record<string, unknown>; enabled: boolean
  }>) => request<FlowSchedule>(`/flows/${id}/schedules/${scheduleId}`, {
    method: 'PUT', body: JSON.stringify(payload),
  }),
  deleteSchedule: (id: string, scheduleId: string) => request<void>(
    `/flows/${id}/schedules/${scheduleId}`, { method: 'DELETE' },
  ),
  createRun: (id: string, inputData: Record<string, unknown>, versionNumber?: number, configOverrides: Record<string, unknown> = {}) => request<RunDetail>(
    `/flows/${id}/runs`,
    { method: 'POST', body: JSON.stringify({ inputData, versionNumber, configOverrides }) },
  ),
  runs: () => request<RunSummary[]>('/runs'),
  run: (id: string) => request<RunDetail>(`/runs/${id}`),
  rerunRun: (id: string) => request<RunDetail>(`/runs/${id}/rerun`, { method: 'POST' }),
  cancelRun: (id: string) => request<RunDetail>(`/runs/${id}/cancel`, { method: 'POST' }),
  retryNode: (runId: string, nodeId: string) => request<RunDetail>(
    `/runs/${runId}/nodes/${nodeId}/retry`,
    { method: 'POST' },
  ),
  resumeNode: (runId: string, nodeId: string, decision: string, comment: string) => request<RunDetail>(
    `/runs/${runId}/nodes/${nodeId}/resume`,
    { method: 'POST', body: JSON.stringify({ decision, comment, data: {} }) },
  ),
}
