const urlInput = document.getElementById("urlInput");
const btnMp4 = document.getElementById("btnMp4");
const btnMp3 = document.getElementById("btnMp3");
const progressWrap = document.getElementById("progressWrap");
const progressFill = document.getElementById("progressFill");
const progressPercent = document.getElementById("progressPercent");
const progressLabel = document.getElementById("progressLabel");
const statusEl = document.getElementById("status");

function setStatus(msg, type = "info") {
  statusEl.textContent = msg;
  statusEl.className = `status ${type}`;
}

function setLoading(loading) {
  btnMp4.disabled = loading;
  btnMp3.disabled = loading;
  urlInput.disabled = loading;
  if (loading) {
    progressWrap.classList.remove("hidden");
    progressFill.style.width = "0%";
    progressPercent.textContent = "0%";
    progressLabel.textContent = "Đang tải...";
  }
}

function updateProgress(percent) {
  const p = Math.min(100, Math.max(0, percent));
  progressFill.style.width = `${p}%`;
  progressPercent.textContent = `${Math.round(p)}%`;
  if (p >= 100) progressLabel.textContent = "Hoàn tất";
}

function parseSseChunk(text, onProgress, onDone, onJson) {
  const lines = text.split("\n");
  for (const line of lines) {
    if (!line.startsWith("data:")) continue;
    const data = line.replace(/^data:\s?/, "").trim();
    if (!data) continue;
    if (data.startsWith("PROGRESS:")) {
      const pct = parseFloat(data.slice(9));
      if (!Number.isNaN(pct)) onProgress(pct);
    } else if (data.startsWith("DONE:")) {
      onDone(data.slice(5));
    } else if (data.startsWith("{")) {
      try {
        onJson(JSON.parse(data));
      } catch (_) {
        /* ignore non-json */
      }
    }
  }
}

async function download(format) {
  const url = urlInput.value.trim();
  if (!url) {
    setStatus("Vui lòng nhập URL.", "error");
    urlInput.focus();
    return;
  }
  try {
    new URL(url);
  } catch {
    setStatus("URL không hợp lệ.", "error");
    return;
  }

  setLoading(true);
  setStatus("Đang tải qua media_downloader.py...", "info");
  updateProgress(5);

  try {
    const response = await fetch("/api/download", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, format }),
    });

    if (!response.ok) {
      // try text first — may be SSE error or JSON
      const text = await response.text();
      let msg = `HTTP ${response.status}`;
      try {
        const j = JSON.parse(text);
        msg = j.error || msg;
      } catch (_) {
        if (text) msg = text.slice(0, 200);
      }
      throw new Error(msg);
    }

    let finalData = null;
    const contentType = (response.headers.get("content-type") || "").toLowerCase();

    if (contentType.includes("text/event-stream") || contentType.includes("text/plain")) {
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        // process complete lines only
        const parts = buffer.split("\n");
        buffer = parts.pop() || "";
        parseSseChunk(
          parts.join("\n"),
          (pct) => updateProgress(pct),
          () => updateProgress(95),
          (obj) => {
            finalData = obj;
          }
        );
      }
      // flush remaining
      if (buffer.trim()) {
        parseSseChunk(
          buffer,
          (pct) => updateProgress(pct),
          () => updateProgress(95),
          (obj) => {
            finalData = obj;
          }
        );
      }
    } else {
      const text = await response.text();
      try {
        finalData = JSON.parse(text);
      } catch (_) {
        // maybe SSE without proper content-type
        parseSseChunk(
          text,
          (pct) => updateProgress(pct),
          () => updateProgress(95),
          (obj) => {
            finalData = obj;
          }
        );
      }
    }

    if (!finalData || !finalData.success) {
      throw new Error((finalData && finalData.error) || "Tải thất bại — không nhận được kết quả JSON");
    }

    updateProgress(100);

    const link = finalData.download_url || finalData.url;
    const name = finalData.filename
      ? finalData.filename.split(/[/\\]/).pop()
      : "download." + format;

    if (link) {
      const a = document.createElement("a");
      a.href = link;
      a.download = name;
      document.body.appendChild(a);
      a.click();
      a.remove();
    }

    setStatus(
      `Tải thành công: ${name}` + (finalData.codec === "h264" ? " (H.264)" : ""),
      "success"
    );
  } catch (err) {
    setStatus(`Lỗi: ${err.message}`, "error");
    progressWrap.classList.add("hidden");
  } finally {
    setLoading(false);
  }
}

btnMp4.addEventListener("click", () => download("mp4"));
btnMp3.addEventListener("click", () => download("mp3"));
urlInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") download("mp4");
});
