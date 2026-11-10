#!/usr/bin/env bash
# 一键部署 HGhub 播放解析代理（8080 端口）。
#
# 在服务器上运行（需要 root）：
#   bash deploy-proxy.sh
# 或者从仓库根目录：
#   bash scripts/deploy-proxy.sh
#
# 做什么：
#   1. 把 proxy/server.py 复制到 /root/hghub-proxy/server.py
#   2. 写 systemd 服务 /etc/systemd/system/hghub-proxy.service（开机自启）
#   3. 启动并验证：/health、POST /api/play、代理拉 m3u8 清单
#
# 不会动的东西：80 端口的 Xray、443 端口的 MTProto、其他任何服务。
# 依赖：Python 3（Debian 12/13 自带）、systemd。不用 apt，不用 pip。
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
INSTALL_DIR="/root/hghub-proxy"
SERVICE_FILE="/etc/systemd/system/hghub-proxy.service"
PORT="${PORT:-8080}"

if [ "$(id -u)" -ne 0 ]; then
  echo "请用 root 运行：sudo bash $0" >&2
  exit 1
fi

echo "== 1/4 安装代理到 $INSTALL_DIR"
mkdir -p "$INSTALL_DIR"
cp "$REPO_DIR/proxy/server.py" "$INSTALL_DIR/server.py"
python3 -m py_compile "$INSTALL_DIR/server.py" && echo "   server.py 语法检查通过"

echo "== 2/4 写 systemd 服务（开机自启）"
cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=HGhub playback proxy (port $PORT)
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 $INSTALL_DIR/server.py
Restart=always
RestartSec=5
# 代理只监听 8080，不碰 80（Xray）/ 443（MTProto）
Environment=PORT=$PORT

[Install]
WantedBy=multi-user.target
EOF

echo "== 3/4 启动服务"
systemctl daemon-reload
systemctl enable --now hghub-proxy
sleep 2
systemctl is-active --quiet hghub-proxy || {
  echo "服务没有起来，看日志：" >&2
  systemctl status hghub-proxy --no-pager | head -30 >&2
  exit 1
}
echo "   hghub-proxy 已启动：$(systemctl show -p MainPID --value hghub-proxy)"

echo "== 4/4 验证"
echo "   GET /health："
curl -fsS "http://127.0.0.1:$PORT/health" && echo
echo "   POST /api/play {id:12, ep:1}（取《神瞳觉醒》第 1 集播放地址）："
curl -fsS -X POST -H 'Content-Type: application/json' -d '{"id":12,"ep":1}' \
  "http://127.0.0.1:$PORT/api/play" | head -c 300
echo
echo "部署完成：代理跑在 0.0.0.0:$PORT，日志：journalctl -u hghub-proxy -f"
