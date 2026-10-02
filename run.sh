#!/usr/bin/env bash
# Chapter07 · 启动 Pipecat WebRTC 语音助手
set -euo pipefail
cd "$(dirname "$0")"

# Pipecat 需要 Python >= 3.11（runner 依赖 http.HTTPMethod）
PY=""
for c in python3.12 python3.11; do
  if command -v "$c" >/dev/null 2>&1; then
    PY="$c"
    break
  fi
done
if [[ -z "$PY" ]]; then
  echo "需要 Python >= 3.11"
  echo "可用: uv python install 3.12"
  exit 1
fi

if [[ ! -d .venv ]]; then
  "$PY" -m venv .venv
  # shellcheck disable=SC1091
  source .venv/bin/activate
  pip install -U pip
  pip install -r requirements.txt
else
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "已生成 .env，请填入 GOOGLE_API_KEY 后重跑"
  exit 1
fi

# 开发 runner：自定义收音页 + WebRTC
export NLTK_ALLOW_PROXIED_URLOPEN="${NLTK_ALLOW_PROXIED_URLOPEN:-1}"
python server.py "$@"
