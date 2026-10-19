#!/usr/bin/env python3
# fetch.py — local YouTube/TikTok downloader. clean TUI + CLI.
# deps:  pip install -U yt-dlp rich
# needs: ffmpeg on PATH (hoặc thả folder ffmpeg cạnh file này)
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

try:
    import yt_dlp
    from rich.console import Console
    from rich.panel import Panel
    from rich.progress import (
        BarColumn, DownloadColumn, Progress, TextColumn,
        TimeRemainingColumn, TransferSpeedColumn,
    )
    from rich.prompt import Prompt
except ImportError:
    sys.exit("missing deps -> pip install -U yt-dlp rich")

console = Console()

QUALITY_FMT = {
    "best": "bestvideo[vcodec^=avc1]+bestaudio/bestvideo[vcodec^=avc]+bestaudio/bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best",
    "1080": "bestvideo[vcodec^=avc1][height<=1080]+bestaudio/bestvideo+bestaudio/best",
    "720":  "bestvideo[vcodec^=avc1][height<=720]+bestaudio/bestvideo+bestaudio/best",
    "480":  "bestvideo[vcodec^=avc1][height<=480]+bestaudio/bestvideo+bestaudio/best",
}
DEFAULT_DIR = Path.home() / "Videos" / "fetch"
OUTTMPL = "%(uploader,channel,creator|unknown)s - %(title).100s [%(id)s].%(ext)s"


class Settings:
    def __init__(self):
        self.outdir = DEFAULT_DIR
        self.quality = "best"
        self.audio = False
        self.playlist = False
        self.cookies_browser = None

    def ensure(self):
        self.outdir.mkdir(parents=True, exist_ok=True)


class UI:
    def __init__(self):
        self.progress = Progress(
            TextColumn("{task.description}", justify="left"),
            BarColumn(bar_width=None), DownloadColumn(),
            TransferSpeedColumn(), TimeRemainingColumn(),
            console=console,
        )
        self.tasks: dict[str, int] = {}

    def hook(self, d):
        info = d.get("info_dict") or {}
        key = str(info.get("id") or d.get("filename") or "item")
        desc = (info.get("title") or "download")[:64]
        total = d.get("total_bytes") or d.get("total_bytes_estimate")

        if d["status"] == "downloading":
            tid = self.tasks.get(key)
            if tid is None:
                tid = self.progress.add_task(f"[cyan]{desc}[/]", total=total)
                self.tasks[key] = tid
            self.progress.update(
                tid,
                completed=d.get("downloaded_bytes") or 0,
                total=total or self.progress.tasks[tid].total,
            )
        elif d["status"] == "finished":
            tid = self.tasks.get(key)
            if tid is not None:
                self.progress.update(
                    tid,
                    completed=self.progress.tasks[tid].total or 0,
                    description=f"[green]{desc}[/] [dim]· downloaded[/]",
                )


def find_ffmpeg():
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    candidates = [
        Path(os.environ.get("USERPROFILE", "")) / "ffmpeg" / "bin" / "ffmpeg.exe",
        Path("C:/ffmpeg/bin/ffmpeg.exe"),
        Path(__file__).resolve().parent / "ffmpeg" / "bin" / "ffmpeg.exe",
    ]
    for c in candidates:
        if c.is_file():
            return str(c)
    return None


def reencode_h264(ffmpeg: str, src: str) -> tuple[str, str | None]:
    src_path = Path(src)
    if not src_path.is_file():
        return src, f"file not found: {src}"

    out = src_path.with_name(src_path.stem + "_h264.mp4")
    cmd = [
        ffmpeg, "-y", "-i", str(src_path),
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k", "-ac", "2",
        "-movflags", "+faststart", "-pix_fmt", "yuv420p",
        "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
        str(out),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        if proc.returncode != 0 or not out.is_file():
            err = (proc.stderr or proc.stdout or "ffmpeg failed")[-800:]
            return src, err
        try:
            if src_path.resolve() != out.resolve() and src_path.exists():
                src_path.unlink()
        except OSError:
            pass
        
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


def build_opts(s: Settings, ui: UI) -> dict:
    opts = {
        "format": QUALITY_FMT[s.quality] if not s.audio else "bestaudio/best",
        "outtmpl": str(s.outdir / OUTTMPL),
        "noplaylist": not s.playlist,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "concurrent_fragment_downloads": 4,
        "progress_hooks": [ui.hook],
        "retries": 5,
        "fragment_retries": 5,
        "windowsfilenames": True,
        "merge_output_format": "mp4",
    }
    if s.audio:
        opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }]
    if s.cookies_browser:
        opts["cookiesfrombrowser"] = (s.cookies_browser,)
    return opts


def first_line(text: str, limit: int = 160) -> str:
    line = (text or "").strip().splitlines()[0] if text else ""
    return line if len(line) <= limit else line[: limit - 1] + "…"


def collect_paths(info) -> list[str]:
    if not info:
        return []
    if "entries" in info:
        out: list[str] = []
        for entry in info["entries"]:
            out += collect_paths(entry)
        return out
    paths = [d["filepath"] for d in (info.get("requested_downloads") or []) if d.get("filepath")]
    if not paths and info.get("filepath"):
        paths.append(info["filepath"])
    return paths


def run(urls: list[str], s: Settings) -> int:
    ffmpeg = find_ffmpeg()
    ui = UI()
    done: list[str] = []
    failures = 0

    with ui.progress:
        for url in urls:
            try:
                with yt_dlp.YoutubeDL(build_opts(s, ui)) as ydl:
                    info = ydl.extract_info(url, download=True)
                paths = collect_paths(info)
                
                if s.audio:
                    done += paths
                else:
                    for p in paths:
                        tid = ui.progress.add_task(f"[yellow]Đang ép H.264: {Path(p).name}[/]", total=100)
                        ui.progress.update(tid, completed=50)
                        
                        out_path, err = reencode_h264(ffmpeg, p)
                        if err:
                            console.print(f"[red]x[/] Lỗi transcode: {first_line(err)}")
                            done.append(p)
                        else:
                            done.append(out_path)
                            
                        ui.progress.update(
                            tid, completed=100, 
                            description=f"[green]H.264 Done: {Path(out_path).name}[/]"
                        )
                        
            except Exception as e:
                failures += 1
                console.print(f"[red]x[/] {url} — {type(e).__name__}: {first_line(str(e))}")

    for p in done:
        console.print(f"[green]ok[/] {p}")
    if done:
        console.print(f"[dim]{len(done)} file(s) -> {s.outdir}[/]")
    return failures


def banner():
    console.print(Panel.fit(
        "[bold]fetch[/] [dim]·[/] youtube / tiktok -> local files\n"
        "[dim]:s settings  ·  :o folder  ·  :q quit[/]",
        border_style="cyan", padding=(0, 2),
    ))


def state_line(s: Settings):
    mode = "audio · mp3" if s.audio else f"video · mp4 (H.264)"
    console.print(
        f"[dim]→[/] {s.outdir}  [dim]·[/]  {mode}  [dim]·[/]  "
        f"playlist {'on' if s.playlist else 'off'}"
    )


def settings_menu(s: Settings):
    while True:
        console.print(
            f"\n[dim]settings[/]  quality [bold]{s.quality}[/] · "
            f"mode [bold]{'audio (mp3)' if s.audio else 'video (mp4)'}[/] · "
            f"playlist [bold]{'on' if s.playlist else 'off'}[/] · "
            f"cookies [bold]{s.cookies_browser or 'off'}[/]"
        )
        key = Prompt.ask(
            "[dim]q[/]uality [dim]m[/]ode [dim]f[/]older [dim]p[/]laylist "
            "[dim]c[/]ookies [dim]b[/]ack",
            choices=["q", "m", "f", "p", "c", "b"], default="b",
        )
        if key == "q":
            s.quality = Prompt.ask("quality", choices=list(QUALITY_FMT), default=s.quality)
        elif key == "m":
            choice = Prompt.ask("mode", choices=["video", "audio"],
                                default="audio" if s.audio else "video")
            s.audio = choice == "audio"
        elif key == "f":
            s.outdir = Path(Prompt.ask("folder", default=str(s.outdir))).expanduser()
            s.ensure()
        elif key == "p":
            s.playlist = not s.playlist
        elif key == "c":
            b = Prompt.ask("browser (chrome/firefox/edge/safari, empty = off)",
                           default=s.cookies_browser or "")
            s.cookies_browser = b.strip() or None
        else:
            return


def interactive(s: Settings):
    banner()
    
    if not find_ffmpeg():
        console.print("\n[bold red]CẢNH BÁO:[/] Không tìm thấy ffmpeg. Không thể ép H.264 hoặc tách MP3!\n")

    try:
        while True:
            # Bước 1: Chọn định dạng
            console.print("\n[bold cyan]Chọn định dạng tải:[/]")
            console.print("  [1] MP4 (Video - Tự động ép H.264)")
            console.print("  [2] MP3 (Audio)")
            console.print("  [q] Thoát")
            mode = Prompt.ask("Lựa chọn", choices=["1", "2", "q"], default="1")
            
            if mode.lower() == "q":
                console.print("[dim]bye[/]")
                return
                
            s.audio = (mode == "2")
            
            # Bước 2: Nhập URL
            state_line(s)
            raw = Prompt.ask("[bold cyan]url[/]", default="", show_default=False).strip()
            
            if raw in {":q", "q", "quit", "exit"}:
                console.print("[dim]bye[/]")
                return
            if raw == ":s":
                settings_menu(s)
                continue
            if raw == ":o":
                console.print(str(s.outdir))
                continue
                
            urls = [u for u in re.split(r"[\s,]+", raw) if re.match(r"https?://", u)]
            if not urls:
                console.print("[yellow]no valid url in that[/]")
                continue
                
            # Bước 3: Tải (sau khi xong sẽ tự vòng lên Bước 1)
            run(urls, s)
            console.print()
            
    except (EOFError, KeyboardInterrupt):
        console.print("\n[dim]bye[/]")
        return


def parse_args():
    ap = argparse.ArgumentParser(prog="fetch", description="local YouTube/TikTok downloader")
    ap.add_argument("urls", nargs="*", help="one or more video urls")
    ap.add_argument("-o", "--outdir", type=Path, default=DEFAULT_DIR)
    ap.add_argument("-q", "--quality", choices=list(QUALITY_FMT), default="best")
    ap.add_argument("-a", "--audio", action="store_true", help="extract mp3")
    ap.add_argument("-p", "--playlist", action="store_true", help="download whole playlist")
    ap.add_argument("--cookies-from-browser", default=None)
    return ap.parse_args()


def main():
    args = parse_args()
    s = Settings()
    s.outdir = args.outdir.expanduser()
    s.quality = args.quality
    s.audio = args.audio
    s.playlist = args.playlist
    s.cookies_browser = args.cookies_from_browser
    s.ensure()

    if args.urls:
        raise SystemExit(1 if run(args.urls, s) else 0)

    interactive(s)


if __name__ == "__main__":
    main()