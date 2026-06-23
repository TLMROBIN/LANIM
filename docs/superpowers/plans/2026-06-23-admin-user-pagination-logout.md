# Admin User Pagination And Logout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add server-backed pagination to the admin user list and a logout button in the top bar.

**Architecture:** The backend keeps owning the full user query and returns a compact paginated envelope. The frontend tracks user-list pagination state and calls the existing logout endpoint to clear the session.

**Tech Stack:** FastAPI, SQLAlchemy, pytest, Vue 3, TypeScript, Vite.

---

### Task 1: Backend Paginated Users API

**Files:**
- Modify: `backend/tests/test_core.py`
- Modify: `backend/app/main.py`

- [ ] Write a failing pytest that creates more than one page of users and asserts `/api/admin/users?page=2&page_size=2` returns `items`, `total`, `page`, `page_size`, and `pages`.
- [ ] Run `pytest backend/tests/test_core.py::test_admin_users_are_paginated -q` and confirm it fails because the endpoint still returns a list.
- [ ] Update `list_admin_users` to count filtered users and return the paginated envelope.
- [ ] Run the focused pytest and confirm it passes.

### Task 2: Frontend Pagination And Logout

**Files:**
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/App.vue`
- Modify: `frontend/src/style.css`

- [ ] Add a `Paginated<T>` type and update `api.adminUsers` to accept `page`, `pageSize`, and optional `role`.
- [ ] Add `api.logout`.
- [ ] Update `App.vue` to load only the current user page, render pagination controls, refresh after mutations, and clear state on logout.
- [ ] Add small topbar and pager styles.
- [ ] Run `npm --prefix frontend run build` and confirm TypeScript/Vite pass.

### Task 3: Full Verification

**Files:**
- Verify: backend and frontend commands

- [ ] Run `pytest backend/tests/test_core.py -q`.
- [ ] Run `npm --prefix frontend run build`.
- [ ] Inspect `git diff --check`.
