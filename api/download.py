from http.server import BaseHTTPRequestHandler
import json
import re
import urllib.request
import urllib.parse


def _fetch(url, timeout=30):
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
    """
    Prefer H.264 (play) over HEVC (hdplay).
    Windows Movies & TV often cannot decode HEVC without codec pack.
    """
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

    # Order matters: play (usually H.264) first, then wmplay, hdplay last (often HEVC)
    video = d.get("play") or d.get("wmplay") or d.get("hdplay")
    codec_hint = "h264"
    if video and video == d.get("hdplay") and video != d.get("play"):
        codec_hint = "hevc"

    if video:
        return {
            "url": video,
            "filename": f"{title}.mp4",
            "codec": codec_hint,
            # expose alternatives if available
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

    clean = url.split("?")[0]
    if clean != url:
        candidates.append(
            "https://www.tikwm.com/api/?" + urllib.parse.urlencode({"url": clean, "hd": "1"})
        )

    for api_url in candidates:
        try:
            data = _fetch(api_url)
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


class handler(BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

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
