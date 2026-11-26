from http.server import BaseHTTPRequestHandler
import json
import re
import sys
import time
import urllib.request
import urllib.parse
import urllib.error


UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)


def _fetch_json(url, timeout=18, extra_headers=None):
    headers = {
        "User-Agent": UA,
        "Accept": "application/json, */*",
        "Accept-Language": "en-US,en;q=0.9,vi;q=0.8",
        "Referer": "https://www.tikwm.com/",
        "Origin": "https://www.tikwm.com",
    }
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def _safe_name(title):
    t = re.sub(r'[\\/:*?"<>|\n\r#]+', "_", (title or "download")).strip()
    t = re.sub(r"_+", "_", t).strip("._ ")
    return (t[:80] or "download")


def _parse_tikwm(data, fmt):
    """Prefer H.264 (play) over HEVC (hdplay). Return direct CDN URLs."""
    if not isinstance(data, dict) or data.get("code") != 0:
        return None, (data or {}).get("msg") or "parse fail"
    d = data.get("data") or {}
    title = _safe_name(d.get("title") or d.get("id") or "download")

    if fmt == "mp3":
        music = d.get("music")
        if not music and isinstance(d.get("music_info"), dict):
            music = d["music_info"].get("play")
        if music:
            return {
                "url": music,
                "filename": f"{title}.mp3",
                "codec": "audio",
                "direct": True,
            }, None
        return None, "no audio"

    # play â H.264 no-wm; wmplay = with watermark; hdplay often HEVC
    video = d.get("play") or d.get("wmplay") or d.get("hdplay")
    codec_hint = "h264"
    if video and video == d.get("hdplay") and video != d.get("play"):
        codec_hint = "hevc"

    if video:
        return {
            "url": video,
            "filename": f"{title}.mp4",
            "codec": codec_hint,
            "direct": True,
            "urls": {
                "play": d.get("play"),
                "wmplay": d.get("wmplay"),
                "hdplay": d.get("hdplay"),
            },
        }, None
    return None, "no video"


def _tikwm_candidates(url):
    q = urllib.parse.urlencode({"url": url, "hd": "1"})
    c = [
        "https://www.tikwm.com/api/?" + q,
        "https://tikwm.com/api/?" + q,
    ]
    clean = url.split("?")[0].rstrip("/")
    if clean != url.rstrip("/"):
        q2 = urllib.parse.urlencode({"url": clean, "hd": "1"})
        c.append("https://www.tikwm.com/api/?" + q2)
    return c


def _try_tikmate(url, fmt):
    """Secondary resolver. Best-effort, may break anytime."""
    try:
        api = "https://api.tikmate.app/api/lookup?" + urllib.parse.urlencode(
            {"url": url}
        )
        data = _fetch_json(
            api,
            timeout=12,
            extra_headers={
                "Referer": "https://tikmate.app/",
                "Origin": "https://tikmate.app",
            },
        )
        if not isinstance(data, dict):
            return None, "tikmate bad payload"
        token = data.get("token") or data.get("id")
        if fmt == "mp3":
            music = data.get("music") or data.get("audio") or data.get("music_url")
            if music:
                title = _safe_name(data.get("title") or data.get("desc") or "download")
                return {
                    "url": music,
                    "filename": f"{title}.mp3",
                    "codec": "audio",
                    "direct": True,
                    "source": "tikmate",
                }, None
            return None, "tikmate no audio"

        video = (
            data.get("nwm_video_url")
            or data.get("video_url")
            or data.get("play")
            or data.get("url")
        )
        if not video and token:
            video = f"https://tikmate.app/download/{token}.mp4"
        if video:
            title = _safe_name(data.get("title") or data.get("desc") or "download")
            return {
                "url": video,
                "filename": f"{title}.mp4",
                "codec": "h264",
                "direct": True,
                "source": "tikmate",
            }, None
        return None, "tikmate no video"
    except Exception as e:
        return None, f"tikmate: {type(e).__name__}"


def download_media(url, fmt):
    errors = []

    for api_url in _tikwm_candidates(url):
        for attempt in range(2):
            try:
                data = _fetch_json(api_url, timeout=16)
                result, err = _parse_tikwm(data, fmt)
                if result:
                    result["source"] = "tikwm"
                    return {"success": True, **result}
                errors.append(err or "unknown")
            except Exception as e:
                errors.append(str(e))
            time.sleep(0.35 * (attempt + 1))

    result, err = _try_tikmate(url, fmt)
    if result:
        return {"success": True, **result}
    if err:
        errors.append(err)

    is_tt = any(
        x in url.lower()
        for x in ("tiktok.com", "vm.tiktok.com", "vt.tiktok.com", "douyin.com")
    )
    if not is_tt:
        return {
            "success": False,
            "error": "Nguá»n nÃ y (YouTube/khÃ¡c) cáº§n yt-dlp backend. TrÃªn Vercel hiá»n há» trá»£ tá»t nháº¥t lÃ  TikTok.",
            "hint": "DÃ¹ng link TikTok Äáº§y Äá»§ dáº¡ng https://www.tiktok.com/@user/video/ID",
        }

    return {
        "success": False,
        "error": "KhÃ´ng táº£i ÄÆ°á»£c video. " + (errors[0] if errors else "API lá»i."),
        "hint": "Thá»­ láº¡i sau vÃ i giÃ¢y, hoáº·c dÃ¹ng link dáº¡ng /@user/video/1234567890",
        "details": errors[:4],
    }


def _proxy_file(handler, media_url, filename, fmt):
    """Optional stream proxy for browsers that block cross-origin download."""
    headers = {
        "User-Agent": UA,
        "Accept": "*/*",
        "Referer": "https://www.tiktok.com/",
    }
    req = urllib.request.Request(media_url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            content_type = resp.headers.get("Content-Type") or (
                "audio/mpeg" if fmt == "mp3" else "video/mp4"
            )
            chunks = []
            total = 0
            max_bytes = 40 * 1024 * 1024
            while True:
                block = resp.read(64 * 1024)
                if not block:
                    break
                total += len(block)
                if total > max_bytes:
                    handler._json(
                        413,
                        {
                            "success": False,
                            "error": "File quÃ¡ lá»n Äá» proxy (~40MB). DÃ¹ng táº£i trá»±c tiáº¿p.",
                        },
                    )
                    return
                chunks.append(block)
            data = b"".join(chunks)

        safe = _safe_name(filename) or ("download.mp3" if fmt == "mp3" else "download.mp4")
        if not safe.lower().endswith((".mp4", ".mp3", ".m4a")):
            safe += ".mp3" if fmt == "mp3" else ".mp4"

        handler.send_response(200)
        handler.send_header("Content-Type", content_type)
        handler.send_header(
            "Content-Disposition",
            f'attachment; filename="{safe}"; filename*=UTF-8\'\'{urllib.parse.quote(safe)}',
        )
        handler.send_header("Content-Length", str(len(data)))
        handler.send_header("Cache-Control", "no-store")
        handler._cors()
        handler.end_headers()
        handler.wfile.write(data)
    except urllib.error.HTTPError as e:
        handler._json(502, {"success": False, "error": f"CDN lá»i {e.code}. Thá»­ láº¡i sau vÃ i giÃ¢y."})
    except Exception as e:
        handler._json(502, {"success": False, "error": f"Proxy fail: {type(e).__name__}: {e}"})


class handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), format % args))

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        try:
            parsed = urllib.parse.urlparse(self.path)
            qs = urllib.parse.parse_qs(parsed.query)
            if qs.get("proxy", [""])[0] != "1":
                self._json(
                    400,
                    {
                        "success": False,
                        "error": "Use POST to resolve, or GET ?proxy=1&url=... to stream",
                    },
                )
                return
            media_url = (qs.get("url") or [""])[0].strip()
            filename = (qs.get("name") or ["download.mp4"])[0]
            fmt = (qs.get("format") or ["mp4"])[0].lower()
            if not media_url or not re.match(r"^https?://", media_url, re.I):
                self._json(400, {"success": False, "error": "Missing media url"})
                return
            _proxy_file(self, media_url, filename, fmt)
        except Exception as e:
            self._json(500, {"success": False, "error": f"{type(e).__name__}: {e}"})

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length > 50_000:
                self._json(413, {"success": False, "error": "Body quÃ¡ lá»n"})
                return
            body = self.rfile.read(length) if length else b""
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
                self._json(400, {"success": False, "error": "URL pháº£i báº¯t Äáº§u báº±ng http/https"})
                return

            result = download_media(url, fmt)
            code = 200 if result.get("success") else 422
            self._json(code, result)
        except Exception as e:
            self._json(500, {"success": False, "error": f"{type(e).__name__}: {e}"})

    def _json(self, code, obj):
        try:
            payload = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self._cors()
            self.end_headers()
            self.wfile.write(payload)
        except Exception:
            pass
