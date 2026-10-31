// pro-lane-kit · 自动重试
// 只点“错误提示旁边”的“重试”按钮；绝不点“重新生成”，也不点没有错误提示的按钮。
// 同一页两次点击至少隔 45 秒，一小时最多 30 次，防止死循环。
// ⚠ 和自动继续一样属于自动操作，可能违反使用条款。默认关闭；打开方法同 auto-continue.js。
(() => {
  const ENABLED = false; // ← 打开时改这里
  if (!ENABLED || window.__proLaneAutoRetry) return;
  window.__proLaneAutoRetry = true;

  const ERROR_TEXT = /消息传输超时|传输超时|连接已中断|网络错误|发生错误|出错了|Something went wrong|timed out|Network error|connection (was )?interrupted|An error occurred|Error in message stream|at capacity/i;
  const RETRY_LABEL = /^\s*(重试|重新尝试|再试一次|Retry|Try again)\s*$/i;
  const NEVER_CLICK = /重新生成|Regenerate/i;
  const COOLDOWN_MS = 45_000;
  const MAX_PER_HOUR = 30;
  const clicks = [];

  function findRetry() {
    for (const button of document.querySelectorAll("button")) {
      const label = (button.innerText || button.getAttribute("aria-label") || "").trim();
      if (!RETRY_LABEL.test(label) || NEVER_CLICK.test(label) || button.disabled) continue;
      let box = button.parentElement;
      for (let i = 0; i < 4 && box; i += 1, box = box.parentElement) {
        if (ERROR_TEXT.test(box.innerText || "")) return button;
      }
    }
    return null;
  }

  setInterval(() => {
    const now = Date.now();
    while (clicks.length && now - clicks[0] > 3_600_000) clicks.shift();
    if (clicks.length >= MAX_PER_HOUR || (clicks.length && now - clicks[clicks.length - 1] < COOLDOWN_MS)) return;
    const button = findRetry();
    if (!button) return;
    button.click();
    clicks.push(now);
    console.info("[pro-lane-kit] 自动点了重试", new Date().toLocaleTimeString());
  }, 10_000);
})();
