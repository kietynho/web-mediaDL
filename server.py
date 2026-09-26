#!/usr/bin/env python3
"""
Local backend for Media Downloader web UI.
Serves public/ UI and runs media_downloader.py (yt-dlp + H.264).
"""

import json
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

from flask import Flask, request, Response, send_from_directory, send_file, abort

ROOT = Path(__file__).resolve().parent
PUBLIC = ROOT / "public"
DOWNLOADER = ROOT / "media_downloader.py"
DOWNLOADS = ROOT / "downloads"

app = Flask(__name__, static_folder=str(PUBLIC), static_url_path="")


@app.route("/")
def index():
    index_path = PUBLIC / "index.html"
    if not index_path.is_file():
        alt = ROOT / "index.html"
        if alt.is_file():
            return send_file(alt)
        return (
            "<h1>Not Found</h1>"
            "<p>Missing <code>public/index.html</code>.</p>",
            404,
        )
    return send_from_directory(PUBLIC, "index.html")


@app.route("/style.css")
def css():
    if (PUBLIC / "style.css").is_file():
        return send_from_directory(PUBLIC, "style.css")
    abort(404)


@app.route("/main.js")
def js():
    if (PUBLIC / "main.js").is_file():
        return send_from_directory(PUBLIC, "main.js")
    abort(404)


@app.route("/downloads/<path:filename>")
def serve_download(filename):
    # Flask already unquotes path
    path = (DOWNLOADS / filename).resolve()
    try:
        path.relative_to(DOWNLOADS.resolve())
    except ValueError:
        abort(404)
    if not path.is_file():
        # try case-insensitive / partial match on Windows
        matches = list(DOWNLOADS.glob("*"))
        name_lower = filename.lower()
        for m in matches:
            if m.name.lower() == name_lower:
                path = m
                break
        else:
            abort(404)
    return send_file(
        path,
        as_attachment=True,
        download_name=path.name,
        mimetype="video/mp4" if path.suffix.lower() == ".mp4" else None,
    )


@app.route("/api/download", methods=["POST"])
def api_download():
    data = request.get_json(force=True, silent=True) or {}
    url = (data.get("url") or "").strip()
    fmt = (data.get("format") or "mp4").lower()

    if not url:
        return Response(
            json.dumps({"success": False, "error": "Missing URL"}),
            status=400,
            mimetype="application/json",
        )
    if fmt not in ("mp4", "mp3"):
        return Response(
            json.dumps({"success": False, "error": "Format must be mp4 or mp3"}),
            status=400,
            mimetype="application/json",
        )
    if not DOWNLOADER.is_file():
        return Response(
            json.dumps({"success": False, "error": "media_downloader.py not found"}),
            status=500,
            mimetype="application/json",
        )

    DOWNLOADS.mkdir(parents=True, exist_ok=True)

    def generate():
        cmd = [
            sys.executable,
            str(DOWNLOADER),
            url,
            "--format",
            fmt,
            "--output",
            str(DOWNLOADS),
        ]
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            last_json = None
            for raw in proc.stdout:
                line = raw.strip()
                if not line:
                    continue
                # ignore stderr-style check lines mixed in
                if line.startswith("[check]"):
                    continue
                if line.startswith("PROGRESS:"):
                    yield f"data: {line}\n\n"
                elif line.startswith("DONE:"):
                    yield f"data: {line}\n\n"
                elif line.startswith("{"):
                    last_json = line
                    try:
                        obj = json.loads(line)
                        if obj.get("success") and obj.get("filename"):
                            name = Path(obj["filename"]).name
                            # URL-encode # spaces etc so browser download works
                            obj["download_url"] = "/downloads/" + quote(name)
                            obj["filename"] = name
                            line = json.dumps(obj, ensure_ascii=False)
                    except Exception:
                        pass
                    yield f"data: {line}\n\n"
            proc.wait()
            if proc.returncode != 0 and not last_json:
                yield (
                    'data: {"success": false, "error": '
                    f'"Download failed (code {proc.returncode})"}}\n\n'
                )
        except Exception as e:
            yield f"data: {json.dumps({'success': False, 'error': str(e)})}\n\n"

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


if __name__ == "__main__":
    print("ROOT   :", ROOT)
    print("PUBLIC :", PUBLIC, "exists=" + str(PUBLIC.is_dir()))
    print("index  :", (PUBLIC / "index.html").is_file())
    print("Media Downloader → http://127.0.0.1:5000")
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
