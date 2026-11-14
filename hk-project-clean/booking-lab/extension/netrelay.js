// netrelay.js —— 隔离世界里的中继：把 netrec.js（主世界）的 postMessage 转给 background
(() => {
  if (window.__tdabsNetRelay) return;
  window.__tdabsNetRelay = true;

  window.addEventListener("message", (ev) => {
    const d = ev && ev.data;
    if (!d || typeof d !== "object" || !d.__tdabs_net) return;
    const rec = d.__tdabs_net;
    try {
      chrome.runtime.sendMessage({ type: "net", data: rec }, () => void chrome.runtime.lastError);
    } catch (e) { /* ignore */ }
  }, false);
})();
