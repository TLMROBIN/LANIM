#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="${LANIM_PROJECT_ROOT:-/home/yub/文档/trae_projects/LANIM}"
BRANCH="${LANIM_DEPLOY_BRANCH:-main}"
REMOTE="${LANIM_DEPLOY_REMOTE:-origin}"
LOCK_FILE="${LANIM_DEPLOY_LOCK:-/var/lock/lanim-github-deploy.lock}"
PROJECT_USER="${LANIM_PROJECT_USER:-$(stat -c %U "${PROJECT_ROOT}")}"
HEALTH_URL="${LANIM_HEALTH_URL:-http://127.0.0.1:8090/im/}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "This deploy script must run as root because it rebuilds and restarts Docker Compose services." >&2
  exit 1
fi

if [[ ! -d "${PROJECT_ROOT}/.git" ]]; then
  echo "Project root is not a Git checkout: ${PROJECT_ROOT}" >&2
  exit 1
fi

exec 9>"${LOCK_FILE}"
if ! flock -n 9; then
  echo "Another LANIM GitHub deploy is already running."
  exit 0
fi

run_as_project_user() {
  sudo -u "${PROJECT_USER}" env HOME="$(getent passwd "${PROJECT_USER}" | cut -d: -f6)" "$@"
}

compose() {
  if command -v docker-compose >/dev/null 2>&1; then
    docker-compose "$@"
  else
    docker compose "$@"
  fi
}

fetch_health() {
  python3 - "${HEALTH_URL}" <<'PY'
import sys
import urllib.request

try:
    with urllib.request.urlopen(sys.argv[1], timeout=5) as response:
        sys.exit(0 if 200 <= response.status < 400 else 1)
except Exception:
    sys.exit(1)
PY
}

cd "${PROJECT_ROOT}"

if ! run_as_project_user git diff --quiet || ! run_as_project_user git diff --cached --quiet; then
  echo "Refusing to deploy over tracked local changes in ${PROJECT_ROOT}." >&2
  run_as_project_user git status --short >&2
  exit 1
fi

echo "[1/5] Fetch ${REMOTE}/${BRANCH}"
run_as_project_user git fetch --prune "${REMOTE}" "${BRANCH}"

LOCAL_HEAD="$(run_as_project_user git rev-parse HEAD)"
REMOTE_HEAD="$(run_as_project_user git rev-parse "${REMOTE}/${BRANCH}")"

if [[ "${LOCAL_HEAD}" == "${REMOTE_HEAD}" ]]; then
  echo "Already up to date: ${LOCAL_HEAD:0:7}"
else
  echo "[2/5] Fast-forward ${LOCAL_HEAD:0:7} -> ${REMOTE_HEAD:0:7}"
  run_as_project_user git pull --ff-only "${REMOTE}" "${BRANCH}"
fi

echo "[3/5] Build Docker images"
compose build

echo "[4/5] Start Docker Compose services"
if ! compose up -d; then
  echo "Compose up failed. Recreating application containers and retrying once." >&2
  compose rm -sf api feishu-worker web
  compose up -d
fi

echo "[5/5] Verify health"
for _ in 1 2 3 4 5 6 7 8 9 10; do
  if fetch_health; then
    echo "Health check passed at ${HEALTH_URL}."
    echo "Deployed $(run_as_project_user git rev-parse --short HEAD) from GitHub."
    exit 0
  fi
  sleep 2
done

echo "Health check failed after deploy." >&2
compose ps >&2 || true
exit 1
