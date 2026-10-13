# 校园局域网即时通讯系统

FastAPI + Vue 实现的实名学生-教师即时通讯系统。学生通过学校 SSO 登录后实名提问，教师可在 Web 教师端回复；系统会为绑定了飞书 open_id 的教师创建飞书投递记录，并由 worker 推送到飞书。

## 功能

- 学生端：实名登录、直接选择教师、按班级/科目自动路由、文字、图片和拍照提问。
- 教师端：个人收件箱、实时 WebSocket 更新、文字、图片和拍照回复。
- 管理端：维护班级/科目/教师路由、查看飞书投递状态。
- 认证：对接 Keycloak/OIDC，默认 issuer 为 `http://10.50.159.62/auth/realms/school-platform`。
- 部署：PostgreSQL + 本机图片卷 + API + 飞书 worker + Nginx 前端。

## 本地开发

```bash
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt
IM_DATABASE_URL=sqlite:///./dev.db IM_MEDIA_DIR=./backend/media IM_DEV_AUTH_ENABLED=true \
  .venv/bin/uvicorn app.main:create_app --factory --app-dir backend --port 8001 --reload
```

```bash
cd frontend
npm install
npm run dev
```

开发时设置 `IM_DEV_AUTH_ENABLED=true` 后，可用 `POST /api/dev/login` 模拟 SSO 登录。生产部署不要开启这个开关：

```bash
curl -c cookie.txt -X POST http://127.0.0.1:8000/api/dev/login \
  -H 'content-type: application/json' \
  -d '{"sub":"stu-001","preferred_username":"stu-001","name":"张三","attributes":{"role":["student"],"class_id":["高一1班"]}}'
```

## 局域网部署

1. 复制环境变量：

   ```bash
   cp .env.example .env
   ```

2. 在 Keycloak `school-platform` realm 中新增 client，可参考 `deploy/sso-keycloak-client.json`。生产环境必须设置真实 `IM_OIDC_CLIENT_SECRET`。

3. 启动服务：

   ```bash
   docker compose up -d --build
   ```

4. 若接入已有 `10.50.159.62` 网关，可把 `/im/` 反向代理到本项目 `web:80`，并确保 `/api/` 与 `/ws` 支持 WebSocket upgrade。

## 飞书配置

- 在飞书开放平台创建企业自建应用，开启机器人能力。
- 配置 `IM_FEISHU_APP_ID` 和 `IM_FEISHU_APP_SECRET`。
- 应用需要开通“通过手机号或邮箱获取用户 ID”权限，并让应用有权限查看目标教师。
- 事件订阅选择长连接并添加 `im.message.receive_v1`；应用发布后，worker 才会接收教师回复。
- 管理员在教师资料中填写手机号，系统通过飞书通讯录接口自动查询并保存该应用下的 `open_id`；后端接口为 `POST /api/admin/teachers/{teacher_id}/feishu/resolve`。
- worker 会轮询 queued 投递并调用飞书发送消息 API。教师在飞书回复机器人消息后的事件可接入同一套 `/api/dev/feishu/reply` 处理逻辑；若启用官方长连接 SDK，可把收到的事件用 `app.feishu.extract_reply_event()` 解析后写回该接口对应服务。

## 管理员用户维护

- 登录管理员账号后，管理端会显示“添加用户”“任课路由”“用户列表”。
- 添加学生：选择角色 `学生`，填写用户名、姓名、班级、年级；如果该学生来自 Keycloak，可填写 OIDC `sub`，否则留空使用手工账号标识。
- 编辑/删除学生：在“用户列表”点击编辑或删除。已有会话的用户会被保护，避免删除后破坏历史消息。
- 添加教师：选择角色 `教师`，填写用户名、姓名、飞书 `open_id/user_id`，并保持“启用”。
- 指定教师班级：在“任课路由”填写班级、科目，选择教师并保存。学生按班级科目自动分配时会使用这张路由表。

## 从统一认证同步用户

Keycloak 中已经汇总 StudyAgent、edusimu、HighSchoolPhysics 的学生数据后，可以把 realm 用户同步到 IM：

```bash
python -m app.sync_keycloak_users \
  --keycloak-base-url http://10.50.159.62/auth \
  --realm school-platform \
  --admin-user <Keycloak admin> \
  --admin-password <Keycloak admin password>
```

同步规则：

- 使用 Keycloak 用户 `id` 作为 IM `oidc_sub`，保证 OIDC 登录后能命中同一用户。
- 读取 `attributes.role`，大小写归一化后只同步 `student`、`teacher`、`admin`。
- 学生班级优先读取 `attributes.class_id` 或 `attributes.class`，否则回退到 `attributes.grade`。
- 重复同步会 upsert，不会重复创建同一 SSO 用户。

## 验证

```bash
.venv/bin/python -m pytest backend/tests/test_core.py -q
cd frontend && npm run build
```

## 拍照发图

学生和教师输入区的“拍照”按钮沿用 StudyAgent 的原生文件拍照方式（`accept="image/*"`、`capture="environment"`），在支持的手机和平板浏览器上优先打开后置相机；桌面或不支持该提示的浏览器可能显示文件选择器。原有相册/图片选择入口保留。

拍照后可预览或移除图片，确认后点击发送；取消拍照会保留已选图片。发送失败时保留文字、图片及已上传的图片 ID，可重试。使用原生文件拍照，不需要网页通过 `getUserMedia` 请求摄像头，也适用于学校现有 HTTP 访问方式；是否调用相机仍取决于设备和浏览器支持。

## 上传大小与附件保留

IM 容器和学校网关的 IM API 上传请求上限均为 50 MiB（包含表单开销）。部署脚本会更新 `/etc/nginx/sites-enabled/school-platform.conf` 中的 `/im/api/` 限制并验证、重载 Nginx，首次修改保留备份；前端对 413 显示中文提示。

`attachment-cleanup` 服务每月北京时间 1 日 03:00 后检查一次清理，停机后恢复会补跑。默认仅清理超过 30 天的图片文件，可在 `.env` 设置 `IM_ATTACHMENT_RETENTION_DAYS`。初次启用不会立即删除现有附件，从下个月开始运行。聊天文字和附件元信息保留，学生端和教师端显示“附件已清理”，访问已清理图片返回 410。近期消息使用的图片和仍在飞书投递队列中的图片会保留；不删除媒体目录以外的文件。

可先预览候选文件数量及大小：

```bash
docker compose exec -T attachment-cleanup python -m app.attachment_cleanup --dry-run
```

清理统计写入服务日志，月度执行标记保存在媒体卷中，服务重启不会重复执行当月清理。
