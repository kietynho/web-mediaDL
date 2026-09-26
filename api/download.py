from http.server import BaseHTTPRequestHandler
import json
import urllib.request
import urllib.error


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
            data = json.loads(body.decode("utf-8"))
            url = (data.get("url") or "").strip()
            fmt = (data.get("format") or "mp4").lower()

            if not url:
                self._json(400, {"success": False, "error": "Missing URL"})
                return
            if fmt not in ("mp4", "mp3"):
                self._json(400, {"success": False, "error": "Format must be mp4 or mp3"})
                return

            # Proxy to cobalt.tools (works on Vercel serverless)
            payload = {
                "url": url,
                "downloadMode": "audio" if fmt == "mp3" else "auto",
                "filenameStyle": "basic",
            }
            if fmt == "mp3":
                payload["audioFormat"] = "mp3"

            req = urllib.request.Request(
                "https://api.cobalt.tools/",
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                method="POST",
            )

            with urllib.request.urlopen(req, timeout=60) as resp:
                result = json.loads(resp.read().decode("utf-8"))

            if result.get("status") == "error":
                err = result.get("error") or {}
                self._json(400, {
                    "success": False,
                    "error": err.get("code") or result.get("text") or "Download failed",
                })
                return

            download_url = None
            filename = "download"

            if result.get("status") in ("tunnel", "redirect"):
                download_url = result.get("url")
                filename = result.get("filename") or filename
            elif result.get("status") == "picker" and result.get("picker"):
                item = result["picker"][0]
                download_url = item.get("url")
                filename = item.get("filename") or filename
            elif result.get("url"):
                download_url = result["url"]
                filename = result.get("filename") or filename

            if not download_url:
                self._json(500, {"success": False, "error": "No download URL returned"})
                return

            self._json(200, {
                "success": True,
                "url": download_url,
                "filename": filename,
                "format": fmt,
            })

        except urllib.error.HTTPError as e:
            try:
                err_body = e.read().decode("utf-8")
                err_json = json.loads(err_body)
                msg = err_json.get("error", {}).get("code") or err_json.get("text") or str(e)
            except Exception:
                msg = str(e)
            self._json(e.code, {"success": False, "error": msg})
        except Exception as e:
            self._json(500, {"success": False, "error": str(e)})

    def _json(self, code, obj):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self._cors()
        self.end_headers()
        self.wfile.write(json.dumps(obj).encode("utf-8"))
