#!/usr/bin/env python3
"""HGhub clone web server (8081 端口，跑在学长香港服务器上)。

纯 Python 3 标准库 + 系统自带的 openssl 命令行，无第三方依赖。

它只做三件事：
  1. 提供复刻版网页（web/index.html，首页竖屏刷剧、分类/搜索/榜单/
     帖子/详情/登录、播放器）。
  2. 同源反代：浏览器只跟本站说话，本站再去调 8080 的播放代理和
     上游 huangguoai.com —— 这样前端不用直连被挡的 CDN 域名，
     也不用处理跨域。
  3. 封面图代理 /proxy/img：把加密的封面解密成正常 JPEG 再返回
     （见下面的“封面图为什么是加密的”）。

封面图为什么是加密的（2026-11-10 实测）
----------------------------------------
上游图床 pic.drleao.cn 下发的封面不是 JPEG，而是一串 AES 加密字节：
熵 7.999 bit/字节，全文件找不到任何 JPEG/PNG/WebP 文件头，Content-Type
还是 binary/octet-stream。浏览器直接拿它当 <img src> 永远显示不出来，
这就是网页端封面丢失的根因（跟 Referer、防盗链、CDN 封禁都没有关系，
curl 不带 Referer 也能拉到同样的 200，只是内容是密文）。

上游自己的解密协议是公开的（/static/web/js/plugins/crypto-image-protocol.js）：
  AES-128-CBC，无填充（NoPadding）
  decryptKey = "f5d965df75336270"（16 个 ASCII 字符 = 16 字节密钥）
  decryptIV  = "97b60394abc2fbe1"（16 个 ASCII 字符 = 16 字节 IV）
已用 openssl 本地验证：同样参数解密后得到合法 JPEG（FFD8FFE0 开头）。

本服务在服务端解密：/proxy/img 先拉取封面字节，再用 openssl 解密，
返回 image/jpeg。前端 index.html 里所有封面都走 /proxy/img?url=...，
失败时 onerror 换回直连地址再试一次（部分旧图可能是明文）。

路由
----
  GET  /                        -> web/index.html
  GET  /hls.min.js              -> web/hls.min.js（本地 hls.js，不再依赖 CDN）
  GET  /health                  -> {"status": "ok"}
  POST /api/play                -> 反代到 8080 播放代理 POST /api/play
  GET  /proxy/m3u8?url=...      -> 反代到 8080 /proxy/m3u8
  GET  /proxy/key?url=...       -> 反代到 8080 /proxy/key
  GET  /proxy/ts?url=...        -> 反代到 8080 /proxy/ts
  GET  /proxy/img?url=...       -> 拉封面 -> AES 解密 -> 返回图片
  GET  /api/up/<path>?<query>   -> 反代到 https://huangguoai.com/api/<path>
       （查询串原样转发：?page= / cate_id= / q= 都透传，不截断；
        1.2.0.3 分页就靠它：/api/videos 全量 3361 部/169 页，每页 20 条）
  GET  /api/edge/*              -> 反代到 https://huangguoai.com/api/edge/*

环境变量：HOST（默认 0.0.0.0）、PORT（默认 8081）、
PROXY_BASE（默认 http://127.0.0.1:8080）、UPSTREAM（默认 https://huangguoai.com）。
"""

import hashlib
import json
import logging
import os
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8081"))
PROXY_BASE = os.environ.get("PROXY_BASE", "http://127.0.0.1:8080").rstrip("/")
UPSTREAM = os.environ.get("UPSTREAM", "https://huangguoai.com").rstrip("/")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INDEX_PATH = os.path.join(BASE_DIR, "index.html")

USER_AGENT = "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/120 Mobile Safari/537.36"

# 封面解密参数：上游公开的前端协议（crypto-image-protocol.js），
# 与后台配置无关，写死在这里是安全的 —— 它本来就是公开给浏览器的。
IMG_KEY = b"f5d965df75336270"
IMG_IV = b"97b60394abc2fbe1"

# /proxy/img 只放行这几个图床域名，防止被当成任意代理滥用。
IMG_HOSTS = {
    "pic.drleao.cn", "new.drleao.cn", "image.drleao.cn",
    "new2.drleao.cn", "pic.aluxvl.cn", "huangguoai.com",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger("hghub-web")


def _fetch(url, data=None, headers=None, timeout=25):
    req = urllib.request.Request(url, data=data,
                                 method="POST" if data is not None else "GET")
    req.add_header("User-Agent", USER_AGENT)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read(), resp.headers.get_content_type()
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers.get_content_type() if e.headers else "text/plain"


def _sniff_image(data: bytes) -> str:
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return "application/octet-stream"


def _aes_decrypt_cbc_nopad(data: bytes) -> bytes | None:
    """用系统 openssl 解密 AES-128-CBC（无填充）。失败返回 None。"""
    if len(data) == 0 or len(data) % 16 != 0:
        return None
    try:
        proc = subprocess.run(
            ["openssl", "enc", "-d", "-aes-128-cbc", "-nopad",
             "-K", IMG_KEY.hex(), "-iv", IMG_IV.hex()],
            input=data, capture_output=True, timeout=20)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


class _ImgCache:
    """解密后的封面小缓存：同一张图只解密一次（内存 LRU，最多 200 张）。"""

    def __init__(self, limit=200):
        self.limit = limit
        self.store = OrderedDict()

    def get(self, key):
        hit = self.store.get(key)
        if hit is not None:
            self.store.move_to_end(key)
        return hit

    def put(self, key, value):
        self.store[key] = value
        self.store.move_to_end(key)
        while len(self.store) > self.limit:
            self.store.popitem(last=False)


IMG_CACHE = _ImgCache()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        log.info("%s - %s", self.address_string(), fmt % args)

    def _send(self, status, body, content_type="application/json"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, x-cdn-auth")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status, obj):
        self._send(status, json.dumps(obj, ensure_ascii=False), "application/json")

    def _proxy_to(self, url, data=None, headers=None):
        status, body, ctype = _fetch(url, data=data, headers=headers)
        self._send(status, body, content_type=ctype or "application/octet-stream")

    def do_OPTIONS(self):
        self._send(204, b"")

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)

        if path in ("/", "/index.html"):
            try:
                with open(INDEX_PATH, "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            except FileNotFoundError:
                self._json(404, {"status": 0, "msg": "index.html not found"})
            return
        if path == "/hls.min.js":
            # hls.js 本地化（1.2.0.2）：不再依赖 jsdelivr CDN，APK 内也用同一份。
            try:
                with open(os.path.join(BASE_DIR, "hls.min.js"), "rb") as f:
                    self._send(200, f.read(), "application/javascript; charset=utf-8")
            except FileNotFoundError:
                self._json(404, {"status": 0, "msg": "hls.min.js not found"})
            return
        if path == "/health":
            self._json(200, {"status": "ok"})
            return
        if path in ("/proxy/m3u8", "/proxy/key", "/proxy/ts"):
            url = (qs.get("url") or [""])[0]
            if not url:
                self._json(400, {"status": 0, "msg": "missing url"})
                return
            self._proxy_to(PROXY_BASE + path + "?url=" + urllib.parse.quote(url, safe=""))
            return
        if path == "/proxy/img":
            self._proxy_img((qs.get("url") or [""])[0])
            return
        if path.startswith("/api/up/"):
            rest = path[len("/api/up/"):]
            target = f"{UPSTREAM}/api/{rest}"
            if parsed.query:
                target += "?" + parsed.query
            self._proxy_to(target)
            return
        if path.startswith("/api/edge/"):
            target = UPSTREAM + path
            if parsed.query:
                target += "?" + parsed.query
            self._proxy_to(target)
            return
        self._json(404, {"status": 0, "msg": "not found"})

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/play":
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b"{}"
            self._proxy_to(PROXY_BASE + "/api/play", data=body,
                           headers={"Content-Type": "application/json"})
            return
        if parsed.path.startswith("/api/up/"):
            # 1.2.0.4 评论发布：POST /api/up/videos/:id/comments 透传到上游
            rest = parsed.path[len("/api/up/"):]
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b"{}"
            self._proxy_to(f"{UPSTREAM}/api/{rest}", data=body,
                           headers={"Content-Type": "application/json"})
            return
        if parsed.path.startswith("/api/edge/"):
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b"{}"
            self._proxy_to(UPSTREAM + parsed.path, data=body,
                           headers={"Content-Type": "application/json"})
            return
        self._json(404, {"status": 0, "msg": "not found"})

    def _proxy_img(self, url: str) -> None:
        if not url.startswith(("http://", "https://")):
            self._json(400, {"status": 0, "msg": "url must be http(s)"})
            return
        host = (urllib.parse.urlparse(url).hostname or "").lower()
        if host not in IMG_HOSTS:
            self._json(403, {"status": 0, "msg": f"image host not allowed: {host}"})
            return
        cache_key = hashlib.sha256(url.encode()).hexdigest()
        hit = IMG_CACHE.get(cache_key)
        if hit is not None:
            self._send(200, hit[0], content_type=hit[1])
            return
        status, body, _ = _fetch(url, timeout=20)
        if status != 200:
            self._json(status, {"status": 0, "msg": f"upstream HTTP {status}"})
            return
        # 绝大多数封面是 AES 加密字节：先解密，解密出合法图片就返回图片；
        # 已经是明文图片（或解密失败）就原样返回，浏览器自己判断。
        plain = _aes_decrypt_cbc_nopad(body)
        if plain is not None and _sniff_image(plain) != "application/octet-stream":
            ctype = _sniff_image(plain)
            IMG_CACHE.put(cache_key, (plain, ctype))
            self._send(200, plain, content_type=ctype)
            return
        ctype = _sniff_image(body)
        if ctype == "application/octet-stream":
            ctype = "image/jpeg"
        IMG_CACHE.put(cache_key, (body, ctype))
        self._send(200, body, content_type=ctype)


if __name__ == "__main__":
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    log.info("hghub-web listening on %s:%d (proxy -> %s)", HOST, PORT, PROXY_BASE)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
