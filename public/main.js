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

function forceDownload(blobOrUrl, filename) {
  const a = document.createElement("a");
  a.style.display = "none";
  a.download = filename || "download";
  if (typeof blobOrUrl === "string") {
    a.href = blobOrUrl;
  } else {
    a.href = URL.createObjectURL(blobOrUrl);
  }
  document.body.appendChild(a);
  a.click();
  setTimeout(() => {
    if (typeof blobOrUrl !== "string") URL.revokeObjectURL(a.href);
    a.remove();
  }, 1500);
}

async function downloadViaProxy(mediaUrl, filename, format) {
  // Stream through our API so Content-Disposition forces save on PC + mobile
  const proxy =
    "/api/download?proxy=1&url=" +
    encodeURIComponent(mediaUrl) +
    "&name=" +
    encodeURIComponent(filename) +
    "&format=" +
    encodeURIComponent(format);

  updateProgress(40);
  progressLabel.textContent = "Đang tải file...";

  const res = await fetch(proxy, { method: "GET" });
  if (!res.ok) {
    const text = await res.text();
    let msg = `HTTP ${res.status}`;
    try {
      const j = JSON.parse(text);
      msg = j.error || msg;
    } catch (_) {
      if (text) msg = text.slice(0, 180);
    }
    throw new Error(msg);
  }

  updateProgress(85);
  const blob = await res.blob();
  updateProgress(98);
  forceDownload(blob, filename);
  return true;
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
  setStatus("Đang lấy link tải...", "info");
  updateProgress(8);

  try {
    const response = await fetch("/api/download", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, format }),
    });

    const text = await response.text();
    let finalData = null;
    try {
      finalData = JSON.parse(text);
    } catch (_) {
      throw new Error(text.slice(0, 200) || `HTTP ${response.status}`);
    }

    if (!response.ok || !finalData || !finalData.success) {
      throw new Error(
        (finalData && (finalData.error || finalData.hint)) ||
          `HTTP ${response.status}`
      );
    }

    updateProgress(30);

    const mediaUrl = finalData.download_url || finalData.url;
    const name = finalData.filename
      ? finalData.filename.split(/[/\\]/).pop()
      : "download." + format;

    if (!mediaUrl) {
      throw new Error("Không nhận được link media");
    }

    // Proxy stream → blob → force download (PC + mobile)
    await downloadViaProxy(mediaUrl, name, format);

    updateProgress(100);
    setStatus(
      `Đã tải: ${name}` + (finalData.codec === "h264" ? " (H.264)" : ""),
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
