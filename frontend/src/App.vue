<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api } from './api'
import type { Conversation, Message, User } from './types'

const me = ref<User | null>(null)
const teachers = ref<User[]>([])
const conversations = ref<Conversation[]>([])
const messages = ref<Message[]>([])
const selectedConversation = ref<Conversation | null>(null)
const routes = ref<{ id: number; class_id: string; subject: string; teacher_id: number }[]>([])
const feishuStatus = ref<{ worker: string; deliveries: unknown[] } | null>(null)
const error = ref('')
const sending = ref(false)

const studentMode = ref<'direct' | 'route'>('direct')
const selectedTeacherId = ref<number | ''>('')
const subject = ref('物理')
const content = ref('')
const selectedImage = ref<File | null>(null)

const routeClass = ref('高一1班')
const routeSubject = ref('物理')
const routeTeacherId = ref<number | ''>('')

const basePath = import.meta.env.BASE_URL.replace(/\/$/, '')
const loginUrl = `${basePath}/api/auth/oidc/login`
const isStudent = computed(() => me.value?.role === 'student')
const isTeacher = computed(() => me.value?.role === 'teacher')
const isAdmin = computed(() => me.value?.role === 'admin')

async function loadMe() {
  try {
    me.value = await api.me()
  } catch {
    me.value = null
  }
}

async function loadReferenceData() {
  if (!me.value) return
  teachers.value = await api.teachers().catch(() => [])
  if (isTeacher.value) conversations.value = await api.inbox()
  if (isAdmin.value) {
    routes.value = await api.routes()
    feishuStatus.value = await api.feishuStatus()
  }
}

async function refreshMessages(conversation: Conversation) {
  selectedConversation.value = conversation
  messages.value = await api.messages(conversation.id)
}

async function uploadSelectedImage() {
  if (!selectedImage.value) return []
  const image = await api.uploadImage(selectedImage.value)
  selectedImage.value = null
  return [image.id]
}

async function studentSend() {
  if (!content.value.trim()) return
  error.value = ''
  sending.value = true
  try {
    const imageIds = await uploadSelectedImage()
    const payload =
      studentMode.value === 'direct'
        ? { mode: 'direct', teacher_id: selectedTeacherId.value, subject: subject.value, content: content.value, image_ids: imageIds }
        : { mode: 'route', subject: subject.value, content: content.value, image_ids: imageIds }
    const conversation = await api.createConversation(payload)
    content.value = ''
    await refreshMessages(conversation)
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    sending.value = false
  }
}

async function teacherReply() {
  if (!selectedConversation.value || !content.value.trim()) return
  const imageIds = await uploadSelectedImage()
  await api.postMessage(selectedConversation.value.id, { content: content.value, image_ids: imageIds })
  content.value = ''
  await refreshMessages(selectedConversation.value)
}

async function createRoute() {
  if (!routeTeacherId.value) return
  await api.createRoute({ class_id: routeClass.value, subject: routeSubject.value, teacher_id: routeTeacherId.value })
  routes.value = await api.routes()
}

function connectSocket() {
  const protocol = location.protocol === 'https:' ? 'wss' : 'ws'
  const socket = new WebSocket(`${protocol}://${location.host}${basePath}/ws`)
  socket.onmessage = async (event) => {
    const data = JSON.parse(event.data)
    if (data.event === 'conversation.updated' && isTeacher.value) conversations.value = await api.inbox()
    if (data.event === 'message.created' && selectedConversation.value) await refreshMessages(selectedConversation.value)
  }
}

function assetUrl(url: string) {
  return url.startsWith('/api/') ? `${basePath}${url}` : url
}

onMounted(async () => {
  await loadMe()
  await loadReferenceData()
  if (me.value) connectSocket()
})
</script>

<template>
  <main class="shell">
    <header class="topbar">
      <div>
        <p class="eyebrow">School IM</p>
        <h1>校园即时通讯</h1>
      </div>
      <a v-if="!me" class="button primary" :href="loginUrl">通过统一认证登录</a>
      <div v-else class="user-pill">{{ me.display_name }} · {{ me.role }}</div>
    </header>

    <section v-if="!me" class="card">
      <h2>请先登录</h2>
      <p>系统使用学校 Keycloak/OIDC 统一认证。学生实名提问，教师通过 Web 或飞书回复。</p>
    </section>

    <section v-if="error" class="alert">{{ error }}</section>

    <section v-if="isStudent" class="grid">
      <div class="card">
        <h2>发起提问</h2>
        <label>路由方式</label>
        <select v-model="studentMode">
          <option value="direct">直接选择教师</option>
          <option value="route">按班级与科目自动分配</option>
        </select>
        <label v-if="studentMode === 'direct'">教师</label>
        <select v-if="studentMode === 'direct'" v-model="selectedTeacherId">
          <option disabled value="">请选择教师</option>
          <option v-for="teacher in teachers" :key="teacher.id" :value="teacher.id">{{ teacher.display_name }}</option>
        </select>
        <label>科目</label>
        <input v-model="subject" placeholder="物理" />
        <label>问题</label>
        <textarea v-model="content" rows="6" placeholder="请描述你的问题"></textarea>
        <input type="file" accept="image/*" @change="selectedImage = ($event.target as HTMLInputElement).files?.[0] || null" />
        <button class="button primary" :disabled="sending" @click="studentSend">发送给教师</button>
      </div>

      <div class="card">
        <h2>当前会话</h2>
        <p v-if="!selectedConversation" class="muted">发送问题后，这里会显示教师回复。</p>
        <div v-for="message in messages" :key="message.id" class="message" :class="message.sender_role">
          <strong>{{ message.sender_name }}</strong>
          <p>{{ message.content }}</p>
          <img v-for="image in message.images" :key="image.id" :src="assetUrl(image.url)" :alt="image.original_name" />
        </div>
      </div>
    </section>

    <section v-if="isTeacher" class="grid">
      <div class="card">
        <h2>我的收件箱</h2>
        <button class="button" @click="loadReferenceData">刷新</button>
        <button v-for="item in conversations" :key="item.id" class="conversation" @click="refreshMessages(item)">
          <strong>{{ item.student_name }}</strong>
          <span>{{ item.subject || '未指定科目' }} · 未读 {{ item.unread_count }}</span>
          <small>{{ item.last_message?.content }}</small>
        </button>
      </div>
      <div class="card">
        <h2>回复学生</h2>
        <p v-if="!selectedConversation" class="muted">请选择左侧会话。</p>
        <div v-for="message in messages" :key="message.id" class="message" :class="message.sender_role">
          <strong>{{ message.sender_name }} <small>{{ message.source }}</small></strong>
          <p>{{ message.content }}</p>
          <img v-for="image in message.images" :key="image.id" :src="assetUrl(image.url)" :alt="image.original_name" />
        </div>
        <textarea v-model="content" rows="4" placeholder="输入回复"></textarea>
        <input type="file" accept="image/*" @change="selectedImage = ($event.target as HTMLInputElement).files?.[0] || null" />
        <button class="button primary" @click="teacherReply">回复</button>
      </div>
    </section>

    <section v-if="isAdmin" class="grid">
      <div class="card">
        <h2>任课路由</h2>
        <input v-model="routeClass" placeholder="班级，如 高一1班" />
        <input v-model="routeSubject" placeholder="科目，如 物理" />
        <select v-model="routeTeacherId">
          <option disabled value="">选择教师</option>
          <option v-for="teacher in teachers" :key="teacher.id" :value="teacher.id">{{ teacher.display_name }}</option>
        </select>
        <button class="button primary" @click="createRoute">保存路由</button>
        <ul>
          <li v-for="route in routes" :key="route.id">{{ route.class_id }} / {{ route.subject }} → #{{ route.teacher_id }}</li>
        </ul>
      </div>
      <div class="card">
        <h2>飞书状态</h2>
        <p>Worker：{{ feishuStatus?.worker || 'unknown' }}</p>
        <p>投递记录：{{ feishuStatus?.deliveries.length || 0 }}</p>
        <p class="muted">教师飞书 open_id/user_id 通过 Admin API 维护；后续可扩展成表单。</p>
      </div>
    </section>
  </main>
</template>
