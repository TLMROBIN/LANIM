export interface User {
  id: number
  username: string
  display_name: string
  role: 'student' | 'teacher' | 'admin'
  class_id?: string | null
  grade?: string | null
}

export interface ImageAsset {
  id: number
  url: string
  original_name: string
  mime_type: string
  size: number
}

export interface Message {
  id: number
  conversation_id: number
  sender_id: number
  sender_name: string
  sender_role: string
  source: string
  content: string
  created_at: string
  images: ImageAsset[]
}

export interface Conversation {
  id: number
  student_id: number
  student_name: string
  teacher_id: number
  teacher_name: string
  subject?: string | null
  mode: string
  status: string
  unread_count: number
  last_message?: Message | null
}
