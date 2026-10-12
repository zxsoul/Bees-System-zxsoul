#!/usr/bin/env bash
# wikforge 一键部署脚本（本地 → 生产 www.zxsure.com）
#
# 用法（在 Git Bash 中，项目根目录下）：
#   bash deploy.sh "提交信息"   # 提交全部改动 → 推 GitHub → 部署上线
#   bash deploy.sh              # 工作区干净时，直接把当前 HEAD 部署上线
#
# 链路：
#   本地 commit/push → git bundle（只打包服务器缺失的提交）→ scp 上传
#   → 服务器 git pull bundle → docker compose 重建 api/worker/beat/frontend
#   → 健康检查（api /health、域名、alembic 迁移版本）
#
# 为什么用 bundle：服务器直连 GitHub 不稳定（Empty reply from server），
# bundle 走 SSH 传输，不依赖 GitHub；本地 push 失败也不会阻断部署。
#
# 注意：
# - 若本次改动新增了 .env 环境变量，脚本不会同步 .env（含密钥不进 git），
#   请先在服务器上手动补充 /root/wikforge-main/.env 再运行本脚本。
# - 数据库迁移（alembic）在 api 容器启动时自动执行，无需手动跑。

set -euo pipefail

# ─── 配置 ────────────────────────────────────────────────────────────
SERVER="root@101.201.117.21"
SSH_KEY="$HOME/.ssh/wikforge_deploy"
REMOTE_DIR="/root/wikforge-main"
SERVICES="api worker beat frontend"
SSH="ssh -i $SSH_KEY -o ConnectTimeout=15 $SERVER"
SCP="scp -i $SSH_KEY -o ConnectTimeout=15"

cd "$(dirname "$0")"

# ─── 1. 提交 & 推送 GitHub ──────────────────────────────────────────
MSG="${1:-}"
if [ -n "$(git status --porcelain)" ]; then
  if [ -z "$MSG" ]; then
    echo "✗ 有未提交的改动，请提供提交信息：bash deploy.sh \"提交信息\""
    exit 1
  fi
  echo "▶ 提交本地改动：$MSG"
  git add -A
  git commit -m "$MSG"
fi
echo "▶ 推送 GitHub（失败不阻断部署）"
git push origin main || echo "⚠ GitHub 推送失败，继续部署（bundle 不依赖 GitHub）"

LOCAL_REV="$(git rev-parse HEAD)"
echo "  本地 HEAD：$(git log --oneline -1)"

# ─── 2. bundle 传输代码到服务器 ─────────────────────────────────────
echo "▶ 查询服务器当前版本"
SERVER_REV="$($SSH "cd $REMOTE_DIR && git rev-parse HEAD")"
echo "  服务器 HEAD：$SERVER_REV"

if [ "$SERVER_REV" = "$LOCAL_REV" ]; then
  echo "✓ 服务器代码已是最新，跳过代码传输"
else
  BUNDLE="/tmp/wikforge-deploy-$$.bundle"
  if git merge-base --is-ancestor "$SERVER_REV" "$LOCAL_REV" 2>/dev/null; then
    echo "▶ 打包增量提交（$SERVER_REV..$LOCAL_REV）"
    git bundle create "$BUNDLE" "$SERVER_REV..main"
  else
    echo "⚠ 服务器版本不是本地祖先（可能有手工提交），打包完整 main 分支"
    git bundle create "$BUNDLE" main
  fi
  echo "▶ 上传并在服务器应用"
  $SCP "$BUNDLE" "$SERVER:/tmp/wikforge-deploy.bundle"
  $SSH "cd $REMOTE_DIR && git pull /tmp/wikforge-deploy.bundle main && rm -f /tmp/wikforge-deploy.bundle"
  rm -f "$BUNDLE"
  NEW_REV="$($SSH "cd $REMOTE_DIR && git rev-parse HEAD")"
  if [ "$NEW_REV" != "$LOCAL_REV" ]; then
    echo "✗ 部署后服务器版本（$NEW_REV）与本地（$LOCAL_REV）不一致，请检查服务器 git 状态"
    exit 1
  fi
  echo "✓ 服务器代码已更新到 $(git log --oneline -1)"
fi

# ─── 3. 重建容器 ────────────────────────────────────────────────────
echo "▶ 重建容器（$SERVICES），首次约几分钟，有缓存时 2-5 分钟"
$SSH "cd $REMOTE_DIR && docker compose up -d --build $SERVICES"

# ─── 4. 健康检查 ────────────────────────────────────────────────────
echo "▶ 等待 api 就绪（含自动数据库迁移）"
code=""
for _ in $(seq 1 18); do
  code="$($SSH 'curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/health' || true)"
  [ "$code" = "200" ] && break
  sleep 10
done
if [ "$code" != "200" ]; then
  echo "✗ api 3 分钟内未就绪，请登录服务器查看：docker logs wikforge-api --tail 50"
  exit 1
fi

echo "▶ 验证结果："
$SSH "docker ps --format '{{.Names}} {{.Status}}' | grep -E 'wikforge-(api|worker|beat|frontend)'"
$SSH "curl -s -o /dev/null -w '  api /health:      %{http_code}\n' http://localhost:8000/health"
$SSH "curl -s -o /dev/null -w '  www.zxsure.com:   %{http_code}\n' https://www.zxsure.com"
echo "  alembic 版本：  $($SSH 'docker exec wikforge-api alembic current 2>/dev/null | tail -1')"

echo ""
echo "✓ 部署完成：https://www.zxsure.com"
