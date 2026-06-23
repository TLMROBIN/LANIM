# Admin User Pagination And Logout Design

## Goal

管理员用户管理不再一次性渲染超长用户队列，并且所有已登录用户都能从页面顶部退出登录。

## Design

后端 `/api/admin/users` 支持 `page` 和 `page_size` 查询参数，默认返回第一页 20 条。响应从裸数组改为分页对象：`items`、`total`、`page`、`page_size`、`pages`。角色过滤继续通过 `role` 参数工作，排序保持现有 `role, username` 顺序。

前端管理员页维护当前用户页码和分页元数据。用户列表只渲染当前页，并显示总数、页码、上一页和下一页按钮。新增、编辑、删除用户后刷新当前页；如果当前页因为删除变空，前端回退一页再刷新。

顶部登录态区域新增“退出登录”按钮，调用现有 `POST /api/auth/logout`。成功后清空本地登录态、教师/管理员相关数据和当前会话，展示未登录页面。

## Tests

后端增加分页契约测试，覆盖默认分页、指定页大小、总数和角色过滤。前端通过 TypeScript 构建验证 API 类型和 Vue 模板绑定。
