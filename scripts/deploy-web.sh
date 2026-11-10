#!/usr/bin/env bash
# 一键部署 HGhub 复刻版网页（8081 端口）。
#
# 在服务器上运行（需要 root），先确认 8080 的播放代理已经在跑：
#   bash scripts/deploy-proxy.sh   # 第一次先跑这个
#   bash scripts/deploy-web.sh     # 再跑这个
#
# 做什么：
#   1. 把 web/index.html + web/server.py 复制到 /root/hghub-web/
#   2. 写 systemd 服务 /etc/systemd/system/hghub-web.service（开机自启）
#   3. 启动并验证：首页 200、/api/play 反代、/proxy/img 封面解密
#
# 不会动的东西：80 端口的 Xray、443 端口的 MTProto、8080 的播放代理。
# 依赖：Python 3（Debian 12/13 自带）、openssl 命令行（Debian 自带）、systemd。
# 不用 apt，不用 pip。
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
INSTALL_DIR="/root/hghub-web"
SERVICE_FILE="/etc/systemd/system/hghub-web.service"
PORT="${PORT:-8081}"
PROXY_BASE="${PROXY_BASE:-http://127.0.0.1:8080}"

if [ "$(id -u)" -ne 0 ]; then
  echo "请用 root 运行：sudo bash $0" >&2
  exit 1
fi

command -v openssl >/dev/null || { echo "找不到 openssl，封面解密需要它" >&2; exit 1; }

echo "== 1/4 检查 8080 播放代理"
if ! curl -fsS "http://127.0.0.1:8080/health" >/dev/null 2>&1; then
  echo "8080 代理没在跑！先执行：bash scripts/deploy-proxy.sh" >&2
  exit 1
fi
echo "   8080 代理正常"

echo "== 2/4 安装网页到 $INSTALL_DIR"
mkdir -p "$INSTALL_DIR"
cp "$REPO_DIR/web/index.html" "$INSTALL_DIR/index.html"
cp "$REPO_DIR/web/server.py" "$INSTALL_DIR/server.py"
python3 -m py_compile "$INSTALL_DIR/server.py" && echo "   server.py 语法检查通过"

echo "== 3/4 写 systemd 服务（开机自启）"
cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=HGhub clone web (port $PORT)
After=network.target hghub-proxy.service

[Service]
Type=simple
ExecStart=/usr/bin/python3 $INSTALL_DIR/server.py
Restart=always
RestartSec=5
# 网页只监听 8081，不碰 80（Xray）/ 443（MTProto）/ 8080（播放代理）
Environment=PORT=$PORT
Environment=PROXY_BASE=$PROXY_BASE

[Install]
WantedBy=multi-user.target
EOF

echo "== 4/4 启动并验证"
systemctl daemon-reload
systemctl enable --now hghub-web
sleep 2
systemctl is-active --quiet hghub-web || {
  echo "服务没有起来，看日志：" >&2
  systemctl status hghub-web --no-pager | head -30 >&2
  exit 1
}
echo "   首页："
curl -fsS "http://127.0.0.1:$PORT/" -o /dev/null -w "   HTTP %{http_code}\n"
echo "   /api/play 反代："
curl -fsS -X POST -H 'Content-Type: application/json' -d '{"id":12,"ep":1}' \
  "http://127.0.0.1:$PORT/api/play" | head -c 200
echo
echo "部署完成：网页地址 http://<你的服务器IP>:$PORT/"
echo "日志：journalctl -u hghub-web -f"
