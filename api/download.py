from http.server import BaseHTTPRequestHandler
import json
import re
import urllib.request
import urllib.parse
import urllib.error


def _fetch_json(url, timeout=30):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "application/json, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.tikwm.com/",
        "Origin": "https://www.tikwm.com",
    }
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def _safe_name(title):
    t = re.sub(r'[\\/:*?"<>|\n\r]+', "_", (title or "download")).strip()
    return (t[:80] or "download")


def _parse_tikwm(data, fmt):
    """Prefer H.264 (play) over HEVC (hdplay)."""
    if not isinstance(data, dict) or data.get("code") != 0:
        return None, (data or {}).get("msg") or "parse fail"
    d = data.get("data") or {}
    title = _safe_name(d.get("title") or d.get("id") or "download")

    if fmt == "mp3":
        music = d.get("music")
        if not music and isinstance(d.get("music_info"), dict):
            music = d["music_info"].get("play")
        if music:
            return {"url": music, "filename": f"{title}.mp3", "codec": "audio"}, None
        return None, "no audio"

    video = d.get("play") or d.get("wmplay") or d.get("hdplay")
    codec_hint = "h264"
    if video and video == d.get("hdplay") and video != d.get("play"):
        codec_hint = "hevc"

    if video:
        return {
            "url": video,
            "filename": f"{title}.mp4",
            "codec": codec_hint,
            "urls": {
                "play": d.get("play"),
                "wmplay": d.get("wmplay"),
                "hdplay": d.get("hdplay"),
            },
        }, None
    return None, "no video"


def download_media(url, fmt):
    errors = []
    candidates = [
        "https://www.tikwm.com/api/?" + urllib.parse.urlencode({"url": url, "hd": "1"}),
        "https://tikwm.com/api/?" + urllib.parse.urlencode({"url": url, "hd": "1"}),
    ]

    clean = url.split("?")[0].rstrip("/")
    if clean != url:
        candidates.append(
            "https://www.tikwm.com/api/?" + urllib.parse.urlencode({"url": clean, "hd": "1"})
        )

    for api_url in candidates:
        try:
            data = _fetch_json(api_url)
            result, err = _parse_tikwm(data, fmt)
            if result:
                return {"success": True, **result, "source": "tikwm"}
            errors.append(err or "unknown")
        except Exception as e:
            errors.append(str(e))

    is_tt = any(x in url.lower() for x in ("tiktok.com", "vm.tiktok.com", "vt.tiktok.com"))
    if not is_tt:
        return {
            "success": False,
            "error": "Nguồn này (YouTube/khác) cần yt-dlp backend. Trên Vercel hiện hỗ trợ tốt nhất là TikTok.",
            "hint": "Dùng link TikTok đầy đủ dạng https://www.tiktok.com/@user/video/ID",
        }

    return {
        "success": False,
        "error": "Không tải được video. " + (errors[0] if errors else "API lỗi."),
        "hint": "Kiểm tra URL còn public, hoặc thử link dạng /@user/video/1234567890",
        "details": errors[:3],
    }


def _proxy_file(self, media_url, filename, fmt):
    """Stream remote media with Content-Disposition attachment so browser downloads."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "*/*",
        "Referer": "https://www.tiktok.com/",
    }
    req = urllib.request.Request(media_url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=55) as resp:
            content_type = resp.headers.get("Content-Type") or (
                "audio/mpeg" if fmt == "mp3" else "video/mp4"
            )
            # read in chunks to avoid huge memory spike on free plan
            chunks = []
            total = 0
            max_bytes = 45 * 1024 * 1024  # ~45MB safety for Vercel
            while True:
                block = resp.read(64 * 1024)
                if not block:
                    break
                total += len(block)
                if total > max_bytes:
                    self._json(413, {"success": False, "error": "File quá lớn để proxy qua Vercel (giới hạn ~45MB)."})
                    return
                chunks.append(block)
            data = b"".join(chunks)

        safe = _safe_name(filename) or ("download.mp3" if fmt == "mp3" else "download.mp4")
        if not safe.lower().endswith((".mp4", ".mp3", ".m4a")):
            safe += ".mp3" if fmt == "mp3" else ".mp4"

        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header(
            "Content-Disposition",
            f'attachment; filename="{safe}"; filename*=UTF-8\'\'{urllib.parse.quote(safe)}',
        )
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self._cors()
        self.end_headers()
        self.wfile.write(data)
    except urllib.error.HTTPError as e:
        self._json(502, {"success": False, "error": f"CDN lỗi {e.code}"})
    except Exception as e:
        self._json(502, {"success": False, "error": f"Proxy fail: {e}"})


class handler(BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        """Proxy: /api/download?proxy=1&url=MEDIA&name=file.mp4&format=mp4"""
        try:
            parsed = urllib.parse.urlparse(self.path)
            qs = urllib.parse.parse_qs(parsed.query)
            if qs.get("proxy", [""])[0] != "1":
                self._json(400, {"success": False, "error": "Use POST for resolve, or ?proxy=1 for stream"})
                return
            media_url = (qs.get("url") or [""])[0].strip()
            filename = (qs.get("name") or ["download.mp4"])[0]
            fmt = (qs.get("format") or ["mp4"])[0].lower()
            if not media_url or not re.match(r"^https?://", media_url, re.I):
                self._json(400, {"success": False, "error": "Missing media url"})
                return
            _proxy_file(self, media_url, filename, fmt)
        except Exception as e:
            self._json(500, {"success": False, "error": str(e)})

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            data = json.loads(body.decode("utf-8")) if body else {}
            url = (data.get("url") or "").strip()
            fmt = (data.get("format") or "mp4").lower()

            if not url:
                self._json(400, {"success": False, "error": "Missing URL"})
                return
            if fmt not in ("mp4", "mp3"):
                self._json(400, {"success": False, "error": "Format must be mp4 or mp3"})
                return
            if not re.match(r"^https?://", url, re.I):
                self._json(400, {"success": False, "error": "URL phải bắt đầu bằng http/https"})
                return

            result = download_media(url, fmt)
            code = 200 if result.get("success") else 422
            self._json(code, result)
        except Exception as e:
            self._json(500, {"success": False, "error": str(e)})

    def _json(self, code, obj):
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self._cors()
        self.end_headers()
        self.wfile.write(json.dumps(obj, ensure_ascii=False).encode("utf-8"))
