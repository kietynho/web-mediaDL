#!/usr/bin/env python3
"""
Media Downloader Tool
yt-dlp + ffmpeg. ALWAYS re-encodes video to H.264 + AAC for Windows.

Usage:
  python media_downloader.py <url> --format mp4
  python media_downloader.py <url> --format mp3
"""

import argparse
import sys
import os
import json
import shutil
import subprocess
from pathlib import Path

try:
    import yt_dlp
except ImportError:
    print(json.dumps({"error": "yt-dlp not installed. Run: pip install yt-dlp"}))
    sys.exit(1)


def progress_hook(d):
    if d["status"] == "downloading":
        total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
        downloaded = d.get("downloaded_bytes", 0)
        if total > 0:
            percent = min(85.0, (downloaded / total) * 85)
            print(f"PROGRESS:{percent:.1f}", flush=True)
        else:
            print("PROGRESS:0", flush=True)
    elif d["status"] == "finished":
        print("PROGRESS:85", flush=True)
        print(f"DONE:{d.get('filename', '')}", flush=True)


def find_ffmpeg():
    """Find ffmpeg executable (PATH or common Windows locations)."""
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    # common fallbacks on Windows if PATH not refreshed
    candidates = [
        Path(os.environ.get("USERPROFILE", "")) / "ffmpeg" / "bin" / "ffmpeg.exe",
        Path("C:/ffmpeg/bin/ffmpeg.exe"),
        Path(__file__).resolve().parent / "ffmpeg-9.0.2-essentials_build" / "bin" / "ffmpeg.exe",
        Path(__file__).resolve().parent / "ffmpeg" / "bin" / "ffmpeg.exe",
    ]
    for c in candidates:
        if c.is_file():
            return str(c)
    return None


def probe_codec(ffmpeg: str, path: str) -> str:
    try:
        r = subprocess.run(
            [
                ffmpeg.replace("ffmpeg", "ffprobe") if "ffmpeg" in ffmpeg else "ffprobe",
                "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=codec_name",
                "-of", "default=noprint_wrappers=1:nokey=1",
                path,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return (r.stdout or "").strip().lower()
    except Exception:
        return ""


def reencode_h264(ffmpeg: str, src: str) -> tuple:
    """
    Force re-encode to H.264 + AAC.
    Returns (output_path, error_or_None).
    """
    src_path = Path(src)
    if not src_path.is_file():
        return src, f"file not found: {src}"

    out = src_path.with_name(src_path.stem + "_h264.mp4")
    cmd = [
        ffmpeg, "-y", "-i", str(src_path),
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-c:a", "aac",
        "-b:a", "128k",
        "-ac", "2",
        "-movflags", "+faststart",
        "-pix_fmt", "yuv420p",
        "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
        str(out),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        if proc.returncode != 0 or not out.is_file():
            err = (proc.stderr or proc.stdout or "ffmpeg failed")[-800:]
            return src, err
        # remove original if different
        try:
            if src_path.resolve() != out.resolve() and src_path.exists():
                src_path.unlink()
        except OSError:
            pass
        # rename to clean .mp4 name
        final = src_path.with_suffix(".mp4")
        if final.resolve() != out.resolve():
            if final.exists():
                final.unlink()
            out.rename(final)
            return str(final), None
        return str(out), None
    except subprocess.TimeoutExpired:
        return src, "ffmpeg timeout"
    except Exception as e:
        return src, str(e)


def resolve_downloaded_file(filename: str, output_dir: str) -> str:
    if filename and os.path.exists(filename):
        return filename
    base = os.path.splitext(filename or "")[0]
    for ext in (".mp4", ".mkv", ".webm", ".mov", ".m4a", ".mp3"):
        cand = base + ext
        if os.path.exists(cand):
            return cand
    files = sorted(
        Path(output_dir).glob("*"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for f in files:
        if f.is_file() and f.suffix.lower() in (".mp4", ".mkv", ".webm", ".mov"):
            return str(f)
    return filename


def download(url: str, fmt: str, output_dir: str = "downloads") -> dict:
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    ffmpeg = find_ffmpeg()

    if fmt == "mp3":
        if not ffmpeg:
            return {
                "success": False,
                "error": "ffmpeg not found. Add ffmpeg\\bin to PATH and reopen terminal.",
            }
        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": os.path.join(output_dir, "%(title).80B.%(ext)s"),
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }
            ],
            "progress_hooks": [progress_hook],
            "quiet": True,
            "no_warnings": True,
            "restrictfilenames": True,
        }
    else:
        ydl_opts = {
            "format": (
                "bestvideo[vcodec^=avc1]+bestaudio/"
                "bestvideo[vcodec^=avc]+bestaudio/"
                "bestvideo[ext=mp4]+bestaudio[ext=m4a]/"
                "bestvideo+bestaudio/best"
            ),
            "outtmpl": os.path.join(output_dir, "%(title).80B.%(ext)s"),
            "merge_output_format": "mp4",
            "progress_hooks": [progress_hook],
            "quiet": True,
            "no_warnings": True,
            "restrictfilenames": True,
        }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info.get("title", "download")
            filename = ydl.prepare_filename(info)

            if fmt == "mp3":
                base = os.path.splitext(filename)[0]
                filename = base + ".mp3"
                if not os.path.exists(filename):
                    filename = resolve_downloaded_file(filename, output_dir)
                print("PROGRESS:100", flush=True)
                return {
                    "success": True,
                    "title": title,
                    "filename": filename,
                    "format": "mp3",
                    "codec": "mp3",
                    "ffmpeg": bool(ffmpeg),
                }

            filename = resolve_downloaded_file(filename, output_dir)
            print("PROGRESS:88", flush=True)

            if not ffmpeg:
                print("PROGRESS:100", flush=True)
                return {
                    "success": True,
                    "title": title,
                    "filename": filename,
                    "format": "mp4",
                    "codec": "unknown",
                    "ffmpeg": False,
                    "warning": "ffmpeg not found — file may be HEVC. Install ffmpeg and re-download.",
                }

            print("PROGRESS:90", flush=True)
            out_path, err = reencode_h264(ffmpeg, filename)
            print("PROGRESS:100", flush=True)

            if err:
                return {
                    "success": True,
                    "title": title,
                    "filename": out_path,
                    "format": "mp4",
                    "codec": "unknown",
                    "ffmpeg": True,
                    "warning": f"re-encode failed, original kept: {err[:200]}",
                }

            return {
                "success": True,
                "title": title,
                "filename": out_path,
                "format": "mp4",
                "codec": "h264",
                "ffmpeg": True,
            }
    except Exception as e:
        return {"success": False, "error": str(e)}


def main():
    parser = argparse.ArgumentParser(description="Media Downloader")
    parser.add_argument("url", help="URL to download")
    parser.add_argument("--format", choices=["mp4", "mp3"], default="mp4")
    parser.add_argument("--output", default="downloads")
    args = parser.parse_args()

    # quick self-check printed to stderr so it does not break JSON stdout
    ff = find_ffmpeg()
    print(f"[check] ffmpeg={ff or 'NOT FOUND'}", file=sys.stderr, flush=True)

    result = download(args.url, args.format, args.output)
    print(json.dumps(result, ensure_ascii=False), flush=True)
    if not result.get("success"):
        sys.exit(1)


if __name__ == "__main__":
    main()
