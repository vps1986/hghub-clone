#!/usr/bin/env python3
"""HGhub playback parse proxy (学长香港服务器, 8080 端口).

Pure Python 3 stdlib, no third-party dependencies.

Why this exists
---------------
The old playback endpoint GET /api/videos/:id/play now returns an empty
video_url. The current upstream flow is:

    POST https://huangguoai.com/api/videos/:id/episodes/:ep/playback
    Content-Type: application/json
    Body: {}          <- must be an EMPTY JSON object; sending id/ep in the
                        body makes upstream answer {"status":0,"msg":"播放参数无效"}

On success data.streams.main is the HLS manifest URL, data.cdn.authKey is
"x-cdn-auth", manifest_method is "GET". The manifest is AES-128 encrypted
(16-byte key). The manifest lives on yd-lphu-long.hzjvlo.cn while the key
and .ts segments live on tp4.drleao.cn (HEVC: tp8.drleao.cn), so a browser
that cannot reach those CDN domains cannot play even though the API works.

This proxy therefore does two things:
  1. POST /api/play -- calls upstream playback server-side and returns data.
  2. GET  /proxy?url=... -- fetches a manifest / key / segment server-side
     and relays it with CORS headers, so the client only talks to this host.

Run on your own server (YOUR_SERVER_IP) on port 8080. Do NOT touch the
Xray VLESS listener on port 80 or the mtg MTProto listener on port 443.

Endpoints (与部署版一致，2026-11-10 实测)
---------
  GET  /health            -> {"status": "ok"}（部署版返回纯文本 ok，本版返回
                             JSON，二者都被 /health 检查接受）
  POST /api/play          {"id": 12, "ep": 1} -> 上游 playback 的完整响应
                             （含 data.streams / data.cdn / data.manifest_method，
                             原样返回，不拆包）
  GET  /proxy/m3u8?url=<完整地址>  代理拉 HLS 清单
  GET  /proxy/key?url=<完整地址>   代理拉 AES-128 密钥（16 字节）
  GET  /proxy/ts?url=<完整地址>    代理拉 .ts 分片
  GET  /proxy?url=<完整地址>       以上三者的统一入口（兼容别名）
     全部带 Access-Control-Allow-Origin: *；只放行白名单域名
"""

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8080"))

UPSTREAM = "https://huangguoai.com"
USER_AGENT = "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/120 Mobile Safari/537.36"

# Only these hosts may be relayed through /proxy.
ALLOWED_HOSTS = {
    "huangguoai.com",
    "huangguoai.ai",
    "pic.drleao.cn",
    "yd-lphu-long.hzjvlo.cn",
    "tp1.drleao.cn",
    "tp4.drleao.cn",
    "tp8.drleao.cn",
}
ALLOWED_SUFFIXES = (".hzjvlo.cn", ".drleao.cn")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger("hghub-proxy")


def _host_allowed(host: str) -> bool:
    host = (host or "").lower()
    return host in ALLOWED_HOSTS or host.endswith(ALLOWED_SUFFIXES)


def _fetch(url: str, data: bytes | None = None, headers: dict | None = None,
           timeout: int = 20) -> tuple[int, bytes, str]:
    req = urllib.request.Request(url, data=data, method="POST" if data is not None else "GET")
    req.add_header("User-Agent", USER_AGENT)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read(), resp.headers.get_content_type()
    except urllib.error.HTTPError as e:
        return e.code, e.read(), "text/plain"


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # keep logs in our own format
        log.info("%s - %s", self.address_string(), fmt % args)

    def _send(self, status: int, body: bytes, content_type: str = "application/json"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, x-cdn-auth")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, obj) -> None:
        self._send(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def do_OPTIONS(self):
        self._send(204, b"")

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/health":
            self._json(200, {"status": "ok"})
            return
        if parsed.path == "/proxy":
            qs = urllib.parse.parse_qs(parsed.query)
            url = (qs.get("url") or [""])[0]
            self._proxy(url)
            return
        if parsed.path in ("/proxy/m3u8", "/proxy/key", "/proxy/ts"):
            # 与部署版一致的分路径入口：m3u8 / key / ts 各走各的。
            qs = urllib.parse.parse_qs(parsed.query)
            url = (qs.get("url") or [""])[0]
            self._proxy(url)
            return
        self._json(404, {"status": 0, "msg": "not found"})

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/api/play":
            self._json(404, {"status": 0, "msg": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length) or b"{}")
            video_id = int(payload["id"])
            ep = int(payload.get("ep", 1))
        except Exception as exc:
            self._json(400, {"status": 0, "msg": f"invalid body: {exc}"})
            return

        # Upstream playback: POST with an EMPTY JSON body. Any id/ep in the
        # body makes upstream reject the request ("播放参数无效").
        url = f"{UPSTREAM}/api/videos/{video_id}/episodes/{ep}/playback"
        status, body, _ = _fetch(
            url, data=b"{}", headers={"Content-Type": "application/json"})
        if status != 200:
            self._json(status, {"status": 0, "msg": f"upstream HTTP {status}"})
            return
        try:
            data = json.loads(body)
        except Exception:
            self._json(502, {"status": 0, "msg": "upstream returned non-JSON"})
            return
        # 与部署版一致：把上游响应原样返回（含 data 包装），不拆包。
        # 前端两种形状都兼容：(d.data && d.data.streams) || d.streams。
        self._json(200, data)

    def _proxy(self, url: str) -> None:
        if not url.startswith(("http://", "https://")):
            self._json(400, {"status": 0, "msg": "url must be http(s)"})
            return
        host = urllib.parse.urlparse(url).hostname or ""
        if not _host_allowed(host):
            self._json(403, {"status": 0, "msg": f"host not allowed: {host}"})
            return
        status, body, ctype = _fetch(url, timeout=30)
        # 注意：清单地址后面带 ?auth_key=... 签名参数，要先去掉查询串再判断后缀。
        url_path = urllib.parse.urlsplit(url).path.lower()
        if url_path.endswith(".m3u8") or "mpegurl" in ctype or "x-mpegurl" in ctype:
            ctype = "application/vnd.apple.mpegurl"
        elif url_path.endswith(".ts"):
            ctype = "video/MP2T"
        elif url_path.endswith(".key") or "crypt.key" in url:
            ctype = "application/octet-stream"
        self._send(status, body, content_type=ctype)


if __name__ == "__main__":
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    log.info("hghub-proxy listening on %s:%d", HOST, PORT)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
