import type { AdminUser, Conversation, Message, Paginated, User } from './types'

const basePath = import.meta.env.BASE_URL.replace(/\/$/, '')
const apiPath = `${basePath}/api`

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    credentials: 'include',
    headers: options.body instanceof FormData ? undefined : { 'Content-Type': 'application/json' },
    ...options
  })
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: response.statusText }))
    throw new Error(body.detail || response.statusText)
  }
  return response.json()
}

function queryString(params: Record<string, string | number | undefined>) {
  const query = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== '') query.set(key, String(value))
  }
  const text = query.toString()
  return text ? `?${text}` : ''
}

export const api = {
  me: () => request<User>(`${apiPath}/me`),
  logout: () => request<{ ok: boolean }>(`${apiPath}/auth/logout`, { method: 'POST' }),
  teachers: () => request<User[]>(`${apiPath}/teachers`),
  subjects: (classId: string) => request<{ subject: string; teacher_id: number }[]>(`${apiPath}/subjects?class_id=${encodeURIComponent(classId)}`),
  createConversation: (payload: unknown) => request<Conversation>(`${apiPath}/conversations`, { method: 'POST', body: JSON.stringify(payload) }),
  messages: (id: number) => request<Message[]>(`${apiPath}/conversations/${id}/messages`),
  postMessage: (id: number, payload: unknown) => request<Message>(`${apiPath}/conversations/${id}/messages`, { method: 'POST', body: JSON.stringify(payload) }),
  inbox: () => request<Conversation[]>(`${apiPath}/teacher/inbox`),
  uploadImage: async (file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<{ id: number; url: string }>(`${apiPath}/uploads/images`, { method: 'POST', body: form })
  },
  routes: () => request<{ id: number; class_id: string; subject: string; teacher_id: number }[]>(`${apiPath}/admin/routes`),
  createRoute: (payload: unknown) => request(`${apiPath}/admin/routes`, { method: 'POST', body: JSON.stringify(payload) }),
  adminUsers: (params: { role?: string; page?: number; pageSize?: number } = {}) =>
    request<Paginated<AdminUser>>(
      `${apiPath}/admin/users${queryString({ role: params.role, page: params.page, page_size: params.pageSize })}`
    ),
  createAdminUser: (payload: unknown) => request<AdminUser>(`${apiPath}/admin/users`, { method: 'POST', body: JSON.stringify(payload) }),
  updateAdminUser: (id: number, payload: unknown) => request<AdminUser>(`${apiPath}/admin/users/${id}`, { method: 'PUT', body: JSON.stringify(payload) }),
  deleteAdminUser: (id: number) => request<{ ok: boolean }>(`${apiPath}/admin/users/${id}`, { method: 'DELETE' }),
  syncAdminUsers: (payload: unknown) => request<{ created: number; updated: number; skipped: number }>(`${apiPath}/admin/users/sync`, { method: 'POST', body: JSON.stringify(payload) }),
  feishuStatus: () => request<{ worker: string; deliveries: unknown[] }>(`${apiPath}/admin/feishu/status`)
}
