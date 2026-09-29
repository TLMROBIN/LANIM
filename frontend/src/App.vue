<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { api } from './api'
import type { AdminUser, Conversation, Message, User } from './types'

type RouteClassOption = { key: string; label: string; classIds: string[] }

const me = ref<User | null>(null)
const teachers = ref<User[]>([])
const conversations = ref<Conversation[]>([])
const messages = ref<Message[]>([])
const selectedConversation = ref<Conversation | null>(null)
const routes = ref<{ id: number; class_id: string; subject: string; teacher_id: number }[]>([])
const feishuStatus = ref<{ worker: string; deliveries: unknown[] } | null>(null)
const adminUsers = ref<AdminUser[]>([])
const adminUserPage = ref(1)
const adminUserPageSize = 20
const adminUserTotal = ref(0)
const adminUserPages = ref(1)
const adminUserFilters = ref({ role: '', classId: '', grade: '', search: '' })
const adminUserOptions = ref<{ classes: string[]; grades: string[] }>({ classes: [], grades: [] })
const selectedAdminUserIds = ref<number[]>([])
const error = ref('')
const routeError = ref('')
const sending = ref(false)
const userSaving = ref(false)
const routeSaving = ref(false)
const routeSavedNotice = ref('')

const studentMode = ref<'direct' | 'route'>('direct')
const selectedTeacherId = ref<number | ''>('')
const subject = ref('物理')
const content = ref('')
const selectedImage = ref<File | null>(null)
const uploadedImageId = ref<number | null>(null)
const studentImageInput = ref<HTMLInputElement | null>(null)
const teacherImageInput = ref<HTMLInputElement | null>(null)
let studentMessagePoller: number | null = null
let studentMessagePollActive = false
let socket: WebSocket | null = null

const routeClasses = ref<string[]>([])
const routeClassInput = ref('')
const manualRouteClassIds = ref<string[]>([])
const routeSubject = ref('物理')
const routeTeacherId = ref<number | ''>('')
const userForm = ref({
  id: 0,
  oidc_sub: '',
  username: '',
  display_name: '',
  role: 'student',
  class_id: '',
  grade: '',
  enabled: true,
  feishu_mobile: '',
  feishu_open_id: '',
  feishu_user_id: ''
})

const basePath = import.meta.env.BASE_URL.replace(/\/$/, '')
const loginUrl = `${basePath}/api/auth/oidc/login`
const isStudent = computed(() => me.value?.role === 'student')
const isTeacher = computed(() => me.value?.role === 'teacher')
const isAdmin = computed(() => me.value?.role === 'admin')
const canGoPreviousUserPage = computed(() => adminUserPage.value > 1)
const canGoNextUserPage = computed(() => adminUserPage.value < adminUserPages.value)
const adminTeachers = computed(() => teachers.value)
const feishuPermissionUrl = computed(() => error.value.match(/https:\/\/open\.feishu\.cn\/app\/[^\s)]+/)?.[0] || '')
const knownClassIds = computed(() =>
  Array.from(
    new Set([
      ...adminUserOptions.value.classes,
      ...adminUsers.value.map((user) => user.class_id || ''),
      ...routes.value.map((route) => route.class_id)
    ])
  )
    .map((classId) => classId.trim())
    .filter(Boolean)
)
const classOptions = computed(() => [...knownClassIds.value].sort((a, b) => a.localeCompare(b, 'zh-Hans-CN')))
const routeClassOptions = computed<RouteClassOption[]>(() => {
  const groups = new Map<string, Set<string>>()
  for (const classId of [...knownClassIds.value, ...manualRouteClassIds.value]) {
    const key = normalizeClassKey(classId)
    if (!key) continue
    const classIds = groups.get(key) ?? new Set<string>()
    classIds.add(classId)
    groups.set(key, classIds)
  }
  return [...groups.entries()]
    .map(([key, classIdSet]) => {
      const classIds = [...classIdSet]
      const label = [...classIds].sort((left, right) => {
        const suffixOrder = Number(right.endsWith('班')) - Number(left.endsWith('班'))
        if (suffixOrder) return suffixOrder
        const spacingOrder = Number(/\s/.test(right)) - Number(/\s/.test(left))
        return spacingOrder || left.localeCompare(right, 'zh-Hans-CN')
      })[0]
      return { key, label, classIds }
    })
    .sort((left, right) => left.label.localeCompare(right.label, 'zh-Hans-CN'))
})
const routeDisplayItems = computed(() => {
  const groups = new Map<string, { classKey: string; subject: string; teacherId: number; classIds: Set<string> }>()
  for (const route of routes.value) {
    const classKey = normalizeClassKey(route.class_id)
    const key = `${classKey}\u0000${route.subject}\u0000${route.teacher_id}`
    const group = groups.get(key) ?? { classKey, subject: route.subject, teacherId: route.teacher_id, classIds: new Set<string>() }
    group.classIds.add(route.class_id)
    groups.set(key, group)
  }
  return [...groups.values()].map((group) => ({
    classLabel: routeClassOptions.value.find((option) => option.key === group.classKey)?.label || group.classKey,
    classIds: [...group.classIds],
    subject: group.subject,
    teacherId: group.teacherId
  }))
})

function normalizeClassKey(classId: string) {
  return classId.trim().replace(/\s+/g, '').replace(/班$/, '')
}

function routeClassLabel(key: string) {
  return routeClassOptions.value.find((option) => option.key === key)?.label || key
}
const currentPageUserIds = computed(() => adminUsers.value.map((user) => user.id))
const selectedAdminUsers = computed(() => adminUsers.value.filter((user) => selectedAdminUserIds.value.includes(user.id)))
const isCurrentPageSelected = computed(
  () => currentPageUserIds.value.length > 0 && currentPageUserIds.value.every((id) => selectedAdminUserIds.value.includes(id))
)

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
  if (isStudent.value) {
    conversations.value = await api.studentInbox()
    const savedId = Number(sessionStorage.getItem(`im:student-conversation:${me.value.id}`))
    const preferredConversation = conversations.value.find((item) => item.id === savedId) || conversations.value[0]
    if (preferredConversation) await refreshMessages(preferredConversation)
  }
  if (isAdmin.value) {
    await loadAdminUsers()
    routes.value = await api.routes()
    adminUserOptions.value = await api.adminUserOptions()
    feishuStatus.value = await api.feishuStatus()
  }
}

async function loadAdminUsers() {
  const page = await api.adminUsers({
    role: adminUserFilters.value.role,
    classId: adminUserFilters.value.classId,
    grade: adminUserFilters.value.grade,
    search: adminUserFilters.value.search,
    page: adminUserPage.value,
    pageSize: adminUserPageSize
  })
  adminUsers.value = page.items
  adminUserTotal.value = page.total
  adminUserPage.value = page.page
  adminUserPages.value = page.pages
  selectedAdminUserIds.value = selectedAdminUserIds.value.filter((id) => page.items.some((user) => user.id === id))
}

async function applyAdminUserFilters() {
  adminUserPage.value = 1
  selectedAdminUserIds.value = []
  await loadAdminUsers()
}

async function changeAdminUserPage(direction: -1 | 1) {
  const nextPage = adminUserPage.value + direction
  if (nextPage < 1 || nextPage > adminUserPages.value) return
  adminUserPage.value = nextPage
  await loadAdminUsers()
}

function clearSessionState() {
  me.value = null
  teachers.value = []
  conversations.value = []
  messages.value = []
  selectedConversation.value = null
  routes.value = []
  feishuStatus.value = null
  adminUsers.value = []
  adminUserPage.value = 1
  adminUserTotal.value = 0
  adminUserPages.value = 1
  selectedAdminUserIds.value = []
  adminUserOptions.value = { classes: [], grades: [] }
}

async function logout() {
  error.value = ''
  let idTokenHint = ''
  try {
    const result = await api.logout()
    idTokenHint = result.id_token_hint || ''
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    clearSessionState()
  }
  const origin = window.location.origin
  const logoutUrl = new URL('/auth/realms/school-platform/protocol/openid-connect/logout', origin)
  logoutUrl.searchParams.set('client_id', 'im')
  if (idTokenHint) {
    logoutUrl.searchParams.set('id_token_hint', idTokenHint)
  }
  logoutUrl.searchParams.set(
    'post_logout_redirect_uri',
    new URL('/directory-admin/api/auth/login', origin).toString()
  )
  window.location.href = logoutUrl.toString()
}

async function refreshMessages(conversation: Conversation) {
  selectedConversation.value = conversation
  if (isStudent.value && me.value) sessionStorage.setItem(`im:student-conversation:${me.value.id}`, String(conversation.id))
  messages.value = await api.messages(conversation.id)
}

function messagePreview(message?: Message | null) {
  if (!message) return ''
  return message.content || (message.images.length ? '图片' : '')
}

async function pollStudentMessages() {
  const conversation = selectedConversation.value
  if (!isStudent.value || !conversation || document.hidden || studentMessagePollActive) return
  studentMessagePollActive = true
  try {
    const latestMessages = await api.messages(conversation.id)
    if (selectedConversation.value?.id !== conversation.id) return
    messages.value = latestMessages
    const summary = conversations.value.find((item) => item.id === conversation.id)
    if (summary && latestMessages.length) summary.last_message = latestMessages[latestMessages.length - 1]
  } catch {
    // Keep the last loaded messages visible while the server is temporarily unavailable.
  } finally {
    studentMessagePollActive = false
  }
}

async function uploadSelectedImage() {
  if (!selectedImage.value) return []
  if (uploadedImageId.value !== null) return [uploadedImageId.value]
  const image = await api.uploadImage(selectedImage.value)
  uploadedImageId.value = image.id
  return [image.id]
}

function clearSelectedImage() {
  selectedImage.value = null
  uploadedImageId.value = null
  if (studentImageInput.value) studentImageInput.value.value = ''
  if (teacherImageInput.value) teacherImageInput.value.value = ''
}

function onImageSelected(event: Event) {
  selectedImage.value = (event.target as HTMLInputElement).files?.[0] || null
  uploadedImageId.value = null
}

function startNewStudentConversation() {
  selectedConversation.value = null
  messages.value = []
  error.value = ''
  if (me.value) sessionStorage.removeItem(`im:student-conversation:${me.value.id}`)
}

async function studentSend() {
  if (!content.value.trim() && !selectedImage.value) return
  error.value = ''
  sending.value = true
  try {
    const imageIds = await uploadSelectedImage()
    let conversation: Conversation
    if (selectedConversation.value) {
      conversation = selectedConversation.value
      const sentMessage = await api.postMessage(conversation.id, { content: content.value, image_ids: imageIds })
      conversation.last_message = sentMessage
    } else {
      const payload =
        studentMode.value === 'direct'
          ? { mode: 'direct', teacher_id: selectedTeacherId.value, subject: subject.value, content: content.value, image_ids: imageIds }
          : { mode: 'route', subject: subject.value, content: content.value, image_ids: imageIds }
      conversation = await api.createConversation(payload)
    }
    content.value = ''
    clearSelectedImage()
    conversations.value = [conversation, ...conversations.value.filter((item) => item.id !== conversation.id)]
    await refreshMessages(conversation)
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    sending.value = false
  }
}

async function teacherReply() {
  if (!selectedConversation.value || (!content.value.trim() && !selectedImage.value)) return
  const imageIds = await uploadSelectedImage()
  await api.postMessage(selectedConversation.value.id, { content: content.value, image_ids: imageIds })
  content.value = ''
  clearSelectedImage()
  await refreshMessages(selectedConversation.value)
}

async function createRoute() {
  routeError.value = ''
  routeSavedNotice.value = ''
  if (routeClasses.value.length === 0) {
    routeError.value = '请先选择或添加至少一个班级。'
    return
  }
  if (!routeTeacherId.value) {
    routeError.value = '请先选择任课教师。'
    return
  }
  if (!routeSubject.value.trim()) {
    routeError.value = '请填写科目。'
    return
  }
  routeSaving.value = true
  try {
    const classIdsToSave = new Set(
      routeClasses.value.flatMap((key) => routeClassOptions.value.find((option) => option.key === key)?.classIds || [routeClassLabel(key)])
    )
    for (const classId of classIdsToSave) {
      await api.createRoute({ class_id: classId, subject: routeSubject.value.trim(), teacher_id: routeTeacherId.value })
    }
    routes.value = await api.routes()
    adminUserOptions.value = await api.adminUserOptions()
    manualRouteClassIds.value = []
    routeSavedNotice.value = `已保存 ${routeClasses.value.length} 个班级路由。`
  } catch (err) {
    routeError.value = err instanceof Error ? err.message : String(err)
    routes.value = await api.routes().catch(() => routes.value)
  } finally {
    routeSaving.value = false
  }
}

function toggleRouteClass(classId: string) {
  routeError.value = ''
  routeSavedNotice.value = ''
  routeClasses.value = routeClasses.value.includes(classId)
    ? routeClasses.value.filter((selected) => selected !== classId)
    : [...routeClasses.value, classId]
}

function addRouteClass() {
  const classId = routeClassInput.value.trim()
  if (!classId) return
  const key = normalizeClassKey(classId)
  if (!key) return
  if (!manualRouteClassIds.value.includes(classId)) manualRouteClassIds.value = [...manualRouteClassIds.value, classId]
  if (!routeClasses.value.includes(key)) routeClasses.value = [...routeClasses.value, key]
  routeClassInput.value = ''
  routeError.value = ''
  routeSavedNotice.value = ''
}

function removeRouteClass(classKey: string) {
  routeClasses.value = routeClasses.value.filter((selected) => selected !== classKey)
  routeError.value = ''
  routeSavedNotice.value = ''
}

function resetUserForm() {
  userForm.value = {
    id: 0,
    oidc_sub: '',
    username: '',
    display_name: '',
    role: 'student',
    class_id: '',
    grade: '',
    enabled: true,
    feishu_mobile: '',
    feishu_open_id: '',
    feishu_user_id: ''
  }
}

function editUser(user: AdminUser) {
  userForm.value = {
    id: user.id,
    oidc_sub: user.oidc_sub,
    username: user.username,
    display_name: user.display_name,
    role: user.role,
    class_id: user.class_id || '',
    grade: user.grade || '',
    enabled: user.teacher_profile?.enabled ?? true,
    feishu_mobile: '',
    feishu_open_id: user.teacher_profile?.feishu_open_id || '',
    feishu_user_id: user.teacher_profile?.feishu_user_id || ''
  }
}

async function saveUser() {
  error.value = ''
  userSaving.value = true
  let savedUser: AdminUser | undefined
  let feishuBindingAttempted = false
  const payload = {
    oidc_sub: userForm.value.oidc_sub || undefined,
    username: userForm.value.username,
    display_name: userForm.value.display_name,
    role: userForm.value.role,
    class_id: userForm.value.class_id || undefined,
    grade: userForm.value.grade || undefined,
    enabled: userForm.value.enabled
  }
  try {
    if (userForm.value.id) {
      savedUser = await api.updateAdminUser(userForm.value.id, payload)
    } else {
      savedUser = await api.createAdminUser(payload)
    }
    if (savedUser.role === 'teacher' && userForm.value.feishu_mobile.trim()) {
      feishuBindingAttempted = true
      await api.resolveTeacherFeishu(savedUser.id, userForm.value.feishu_mobile.trim())
    }
    resetUserForm()
    await loadAdminUsers()
    adminUserOptions.value = await api.adminUserOptions()
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err)
    error.value = feishuBindingAttempted
      ? `用户资料已保存，但飞书绑定未完成：${message}`
      : message
    if (savedUser) {
      await loadAdminUsers().catch(() => undefined)
      adminUserOptions.value = await api.adminUserOptions().catch(() => adminUserOptions.value)
    }
  } finally {
    userSaving.value = false
  }
}

async function deleteUser(user: AdminUser) {
  error.value = ''
  try {
    await api.deleteAdminUser(user.id)
    await loadAdminUsers()
    if (adminUsers.value.length === 0 && adminUserPage.value > 1) {
      adminUserPage.value -= 1
      await loadAdminUsers()
    }
    adminUserOptions.value = await api.adminUserOptions()
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  }
}

function toggleCurrentPageUsers() {
  if (isCurrentPageSelected.value) {
    selectedAdminUserIds.value = selectedAdminUserIds.value.filter((id) => !currentPageUserIds.value.includes(id))
    return
  }
  selectedAdminUserIds.value = Array.from(new Set([...selectedAdminUserIds.value, ...currentPageUserIds.value]))
}

function connectSocket() {
  const protocol = location.protocol === 'https:' ? 'wss' : 'ws'
  socket = new WebSocket(`${protocol}://${location.host}${basePath}/ws`)
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
  if (me.value) {
    connectSocket()
    if (isStudent.value) studentMessagePoller = window.setInterval(() => void pollStudentMessages(), 3000)
  }
})

onBeforeUnmount(() => {
  if (studentMessagePoller !== null) window.clearInterval(studentMessagePoller)
  socket?.close()
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
      <div v-else class="session-actions">
        <div class="user-pill">{{ me.display_name }} · {{ me.role }}</div>
        <button class="button" @click="logout">退出登录</button>
      </div>
    </header>

    <section v-if="!me" class="card">
      <h2>请先登录</h2>
      <p>系统使用学校 Keycloak/OIDC 统一认证。学生实名提问，教师通过 Web 或飞书回复。</p>
    </section>

    <section v-if="error" class="alert" role="alert">
      <span>{{ error }}</span>
      <a
        v-if="feishuPermissionUrl"
        :href="feishuPermissionUrl"
        target="_blank"
        rel="noopener noreferrer"
      >
        打开飞书权限设置并申请 contact:user.id:readonly
      </a>
    </section>

    <section v-if="isStudent" class="grid">
      <div class="card">
        <h2>{{ selectedConversation ? '继续会话' : '发起提问' }}</h2>
        <div v-if="selectedConversation" class="active-conversation">
          <p>当前会话：{{ selectedConversation.teacher_name }} · {{ selectedConversation.subject || '未指定科目' }}</p>
          <button class="button" @click="startNewStudentConversation">新建会话</button>
        </div>
        <template v-else>
          <label>路由方式</label>
          <select v-model="studentMode">
            <option value="direct">直接选择教师</option>
            <option value="route">按班级与科目自动分配</option>
          </select>
          <label v-if="studentMode === 'direct'">教师（仅显示关联本班的教师）</label>
          <select v-if="studentMode === 'direct'" v-model="selectedTeacherId">
            <option disabled value="">请选择教师</option>
            <option v-for="teacher in teachers" :key="teacher.id" :value="teacher.id">{{ teacher.display_name }}</option>
          </select>
          <p v-if="studentMode === 'direct' && teachers.length === 0" class="muted">当前班级尚未关联可联系的教师。</p>
          <label>科目</label>
          <input v-model="subject" placeholder="物理" />
        </template>
        <label>问题</label>
        <textarea v-model="content" rows="6" placeholder="请描述你的问题"></textarea>
        <input ref="studentImageInput" type="file" accept="image/*" @change="onImageSelected" />
        <button class="button primary" :disabled="sending" @click="studentSend">{{ selectedConversation ? '发送消息' : '发送给教师' }}</button>
        <h2 class="student-history-title">我的会话</h2>
        <p v-if="conversations.length === 0" class="muted">发送问题后，会话会保留在这里。</p>
        <button
          v-for="item in conversations"
          :key="item.id"
          class="conversation"
          :class="{ selected: selectedConversation?.id === item.id }"
          @click="refreshMessages(item)"
        >
          <strong>{{ item.teacher_name }}</strong>
          <span>{{ item.subject || '未指定科目' }}</span>
          <small>{{ messagePreview(item.last_message) }}</small>
        </button>
      </div>

      <div class="card">
        <h2>会话消息</h2>
        <p v-if="!selectedConversation" class="muted">发送问题或从左侧选择历史会话。</p>
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
          <small>{{ messagePreview(item.last_message) }}</small>
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
        <input ref="teacherImageInput" type="file" accept="image/*" @change="onImageSelected" />
        <button class="button primary" @click="teacherReply">回复</button>
      </div>
    </section>

    <section v-if="isAdmin" class="grid">
      <div class="card">
        <h2>{{ userForm.id ? '编辑用户' : '添加用户' }}</h2>
        <label>角色</label>
        <select v-model="userForm.role" :disabled="Boolean(userForm.id)">
          <option value="student">学生</option>
          <option value="teacher">教师</option>
          <option value="admin">管理员</option>
        </select>
        <label>SSO/OIDC Sub（可留空，系统会按用户名生成手工账号标识）</label>
        <input v-model="userForm.oidc_sub" :disabled="Boolean(userForm.id)" placeholder="Keycloak 用户 ID 或 manual:xxx" />
        <label>用户名</label>
        <input v-model="userForm.username" placeholder="例如 stu1001 或 teacher-physics" />
        <label>姓名</label>
        <input v-model="userForm.display_name" placeholder="真实姓名" />
        <label>班级（学生必填；教师班级通过右侧任课路由指定）</label>
        <input v-model="userForm.class_id" placeholder="例如 高一1班" />
        <label>年级</label>
        <input v-model="userForm.grade" placeholder="例如 高一" />
        <template v-if="userForm.role === 'teacher'">
          <label>教师启用</label>
          <select v-model="userForm.enabled">
            <option :value="true">启用</option>
            <option :value="false">停用</option>
          </select>
          <label>飞书手机号</label>
          <input v-model="userForm.feishu_mobile" placeholder="填写后自动查询并绑定 Open ID" />
          <small v-if="userForm.feishu_open_id" class="muted">当前已绑定飞书账号；填写新手机号可重新绑定。</small>
        </template>
        <button class="button primary" :disabled="userSaving" @click="saveUser">
          {{ userSaving ? '正在保存…' : '保存用户' }}
        </button>
        <button v-if="userForm.id" class="button" @click="resetUserForm">取消编辑</button>
      </div>

      <div class="card">
        <h2>任课路由</h2>
        <label>班级</label>
        <div v-if="routeClassOptions.length" class="route-class-list">
          <label v-for="option in routeClassOptions" :key="option.key" class="route-class-option">
            <input
              type="checkbox"
              :checked="routeClasses.includes(option.key)"
              @change="toggleRouteClass(option.key)"
            />
            <span>{{ option.label }}</span>
          </label>
        </div>
        <p v-else class="muted">暂无现有班级，可在下方输入班级名添加。</p>
        <div class="route-class-entry">
          <input
            v-model="routeClassInput"
            placeholder="输入班级名，例如 高一1班"
            @keydown.enter.prevent="addRouteClass"
          />
          <button class="button" type="button" :disabled="!routeClassInput.trim()" @click="addRouteClass">添加班级</button>
        </div>
        <div v-if="routeClasses.length" class="route-selected-classes">
          <span v-for="classKey in routeClasses" :key="classKey" class="route-class-chip">
            {{ routeClassLabel(classKey) }}
            <button type="button" :aria-label="`移除 ${routeClassLabel(classKey)}`" @click="removeRouteClass(classKey)">×</button>
          </span>
        </div>
        <input v-model="routeSubject" placeholder="科目，如 物理" />
        <select v-model="routeTeacherId">
          <option disabled value="">选择教师</option>
          <option v-for="teacher in adminTeachers" :key="teacher.id" :value="teacher.id">{{ teacher.display_name }}</option>
        </select>
        <p v-if="adminTeachers.length === 0" class="muted">当前没有可用教师，请先启用教师账号。</p>
        <p v-else-if="routeClasses.length === 0" class="muted">先选择或添加班级，再选择教师保存。</p>
        <p v-else-if="!routeTeacherId" class="muted">请选择任课教师后再保存。</p>
        <p v-else-if="!routeSubject.trim()" class="muted">请填写科目后再保存。</p>
        <p v-if="routeError" class="route-error-note" role="alert">{{ routeError }}</p>
        <button
          class="button primary"
          :disabled="routeSaving"
          @click="createRoute"
        >
          {{ routeSaving ? '正在保存…' : `保存 ${routeClasses.length} 个班级路由` }}
        </button>
        <p v-if="routeSavedNotice" class="success-note" role="status">{{ routeSavedNotice }}</p>
        <ul>
          <li
            v-for="route in routeDisplayItems"
            :key="`${route.classLabel}-${route.subject}-${route.teacherId}`"
            :title="route.classIds.length > 1 ? `原始写法：${route.classIds.join('、')}` : undefined"
          >
            {{ route.classLabel }} / {{ route.subject }} → #{{ route.teacherId }}
          </li>
        </ul>
      </div>

      <div class="card wide">
        <h2>用户列表</h2>
        <div class="filters">
          <label>
            <span>角色</span>
            <select v-model="adminUserFilters.role" @change="applyAdminUserFilters">
              <option value="">全部角色</option>
              <option value="student">学生</option>
              <option value="teacher">教师</option>
              <option value="admin">管理员</option>
            </select>
          </label>
          <label>
            <span>班级</span>
            <select v-model="adminUserFilters.classId" @change="applyAdminUserFilters">
              <option value="">全部班级</option>
              <option v-for="classId in classOptions" :key="classId" :value="classId">{{ classId }}</option>
            </select>
          </label>
          <label>
            <span>年级</span>
            <select v-model="adminUserFilters.grade" @change="applyAdminUserFilters">
              <option value="">全部年级</option>
              <option v-for="grade in adminUserOptions.grades" :key="grade" :value="grade">{{ grade }}</option>
            </select>
          </label>
          <form class="user-search" role="search" @submit.prevent="applyAdminUserFilters">
            <label>
              <span>检索用户</span>
              <input v-model="adminUserFilters.search" type="search" placeholder="姓名、用户名、班级或飞书 ID" />
            </label>
            <button class="button primary" type="submit">检索</button>
            <button
              v-if="adminUserFilters.search"
              class="button"
              type="button"
              @click="adminUserFilters.search = ''; applyAdminUserFilters()"
            >
              清除
            </button>
          </form>
        </div>
        <div class="list-toolbar">
          <p class="muted">
            共 {{ adminUserTotal }} 个用户，第 {{ adminUserPage }} / {{ adminUserPages }} 页，已选 {{ selectedAdminUsers.length }} 个
          </p>
          <div class="row-actions">
            <button class="button" :disabled="adminUsers.length === 0" @click="toggleCurrentPageUsers">
              {{ isCurrentPageSelected ? '取消当前页' : '选择当前页' }}
            </button>
            <button class="button" :disabled="selectedAdminUserIds.length === 0" @click="selectedAdminUserIds = []">清空选择</button>
            <button class="button" @click="loadAdminUsers">刷新用户</button>
            <button class="button" :disabled="!canGoPreviousUserPage" @click="changeAdminUserPage(-1)">上一页</button>
            <button class="button" :disabled="!canGoNextUserPage" @click="changeAdminUserPage(1)">下一页</button>
          </div>
        </div>
        <div class="user-table">
          <div v-for="user in adminUsers" :key="user.id" class="user-row">
            <div class="user-main">
              <input v-model="selectedAdminUserIds" type="checkbox" :value="user.id" :aria-label="`选择 ${user.display_name}`" />
              <div>
                <strong>{{ user.display_name }}</strong>
                <span>{{ user.role }} · {{ user.username }} · {{ user.grade || '无年级' }} · {{ user.class_id || '无班级' }}</span>
                <small v-if="user.teacher_profile">
                  飞书：{{ user.teacher_profile.feishu_open_id || '未绑定' }} · {{ user.teacher_profile.enabled ? '启用' : '停用' }}
                </small>
              </div>
            </div>
            <div class="row-actions">
              <button class="button" @click="editUser(user)">编辑</button>
              <button class="button danger" @click="deleteUser(user)">删除</button>
            </div>
          </div>
          <p v-if="adminUsers.length === 0" class="muted">
            {{ adminUserFilters.search ? '没有匹配的用户。' : '当前页没有用户。' }}
          </p>
        </div>
      </div>

      <div class="card">
        <h2>飞书状态</h2>
        <p>Worker：{{ feishuStatus?.worker || 'unknown' }}</p>
        <p>投递记录：{{ feishuStatus?.deliveries.length || 0 }}</p>
        <p class="muted">教师飞书 open_id/user_id 可在左侧“添加/编辑用户”中维护。配置 APP ID/Secret 后，worker 会启用长连接。</p>
      </div>
    </section>
  </main>
</template>
