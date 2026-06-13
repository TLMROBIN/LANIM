import type { Conversation, Message, User } from './types'

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

export const api = {
  me: () => request<User>(`${apiPath}/me`),
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
  feishuStatus: () => request<{ worker: string; deliveries: unknown[] }>(`${apiPath}/admin/feishu/status`)
}
