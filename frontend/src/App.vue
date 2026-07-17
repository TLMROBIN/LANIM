<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api } from './api'
import type { AdminUser, Conversation, Message, User } from './types'

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
const adminUserFilters = ref({ role: '', classId: '', grade: '' })
const adminUserOptions = ref<{ classes: string[]; grades: string[] }>({ classes: [], grades: [] })
const selectedAdminUserIds = ref<number[]>([])
const error = ref('')
const sending = ref(false)

const studentMode = ref<'direct' | 'route'>('direct')
const selectedTeacherId = ref<number | ''>('')
const subject = ref('物理')
const content = ref('')
const selectedImage = ref<File | null>(null)

const routeClasses = ref<string[]>([])
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
const classOptions = computed(() =>
  Array.from(
    new Set([
      ...adminUserOptions.value.classes,
      ...adminUsers.value.map((user) => user.class_id || ''),
      ...routes.value.map((route) => route.class_id)
    ])
  )
    .filter(Boolean)
    .sort((a, b) => a.localeCompare(b, 'zh-Hans-CN'))
)
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
  try {
    await api.logout()
    clearSessionState()
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
    return
  }
  window.location.href =
    'http://192.168.1.206/auth/realms/school-platform/protocol/openid-connect/logout' +
    '?client_id=im&post_logout_redirect_uri=' +
    encodeURIComponent('http://192.168.1.206/im/')
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
  if (!routeTeacherId.value || routeClasses.value.length === 0) return
  for (const classId of routeClasses.value) {
    await api.createRoute({ class_id: classId, subject: routeSubject.value, teacher_id: routeTeacherId.value })
  }
  routes.value = await api.routes()
  adminUserOptions.value = await api.adminUserOptions()
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
    feishu_open_id: user.teacher_profile?.feishu_open_id || '',
    feishu_user_id: user.teacher_profile?.feishu_user_id || ''
  }
}

async function saveUser() {
  error.value = ''
  const payload = {
    oidc_sub: userForm.value.oidc_sub || undefined,
    username: userForm.value.username,
    display_name: userForm.value.display_name,
    role: userForm.value.role,
    class_id: userForm.value.class_id || undefined,
    grade: userForm.value.grade || undefined,
    enabled: userForm.value.enabled,
    feishu_open_id: userForm.value.feishu_open_id || undefined,
    feishu_user_id: userForm.value.feishu_user_id || undefined
  }
  try {
    if (userForm.value.id) {
      await api.updateAdminUser(userForm.value.id, payload)
    } else {
      await api.createAdminUser(payload)
    }
    resetUserForm()
    await loadAdminUsers()
    adminUserOptions.value = await api.adminUserOptions()
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
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
      <div v-else class="session-actions">
        <div class="user-pill">{{ me.display_name }} · {{ me.role }}</div>
        <button class="button" @click="logout">退出登录</button>
      </div>
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
          <label>飞书 open_id</label>
          <input v-model="userForm.feishu_open_id" placeholder="ou_xxx" />
          <label>飞书 user_id</label>
          <input v-model="userForm.feishu_user_id" placeholder="可选" />
        </template>
        <button class="button primary" @click="saveUser">保存用户</button>
        <button v-if="userForm.id" class="button" @click="resetUserForm">取消编辑</button>
      </div>

      <div class="card">
        <h2>任课路由</h2>
        <label>班级</label>
        <select v-model="routeClasses" multiple size="6">
          <option v-for="classId in classOptions" :key="classId" :value="classId">{{ classId }}</option>
        </select>
        <input v-model="routeSubject" placeholder="科目，如 物理" />
        <select v-model="routeTeacherId">
          <option disabled value="">选择教师</option>
          <option v-for="teacher in adminTeachers" :key="teacher.id" :value="teacher.id">{{ teacher.display_name }}</option>
        </select>
        <button class="button primary" :disabled="routeClasses.length === 0 || !routeTeacherId" @click="createRoute">
          保存 {{ routeClasses.length || '' }} 个班级路由
        </button>
        <ul>
          <li v-for="route in routes" :key="route.id">{{ route.class_id }} / {{ route.subject }} → #{{ route.teacher_id }}</li>
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
          <p v-if="adminUsers.length === 0" class="muted">当前页没有用户。</p>
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
