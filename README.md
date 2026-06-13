# 校园局域网即时通讯系统

FastAPI + Vue 实现的实名学生-教师即时通讯系统。学生通过学校 SSO 登录后实名提问，教师可在 Web 教师端回复；系统会为绑定了飞书 open_id 的教师创建飞书投递记录，并由 worker 推送到飞书。

## 功能

- 学生端：实名登录、直接选择教师、按班级/科目自动路由、文字和图片提问。
- 教师端：个人收件箱、实时 WebSocket 更新、文字和图片回复。
- 管理端：维护班级/科目/教师路由、查看飞书投递状态。
- 认证：对接 Keycloak/OIDC，默认 issuer 为 `http://10.50.159.62/auth/realms/school-platform`。
- 部署：PostgreSQL + 本机图片卷 + API + 飞书 worker + Nginx 前端。

## 本地开发

```bash
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt
IM_DATABASE_URL=sqlite:///./dev.db IM_MEDIA_DIR=./backend/media \
  .venv/bin/uvicorn app.main:create_app --factory --app-dir backend --reload
```

```bash
cd frontend
npm install
npm run dev
```

开发时可用 `POST /api/dev/login` 模拟 SSO 登录：

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
- 管理员通过 `PUT /api/admin/teachers/{teacher_id}` 维护教师的 `feishu_open_id` 或 `feishu_user_id`。
- worker 会轮询 queued 投递并调用飞书发送消息 API。教师在飞书回复机器人消息后的事件可接入同一套 `/api/dev/feishu/reply` 处理逻辑；若启用官方长连接 SDK，可把收到的事件用 `app.feishu.extract_reply_event()` 解析后写回该接口对应服务。

## 验证

```bash
.venv/bin/python -m pytest backend/tests/test_core.py -q
cd frontend && npm run build
```
