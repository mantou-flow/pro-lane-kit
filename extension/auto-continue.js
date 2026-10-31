// pro-lane-kit · 自动继续（10-30 版做法）
// 工作框停下 90 秒、又不在思考时，替你发一句“继续”。
// 只在出现过 lane_checkin（签到）或 inbox.md 的对话里生效；记住这个对话，长对话里旧消息被折叠也照样认得。
// 上限：每框每小时 4 次、每框每天 20 次、所有框每天合计 150 次（存在 localStorage，刷新页面也算数）。
// 你最后一条消息里有【暂停】，就不再碰这个框；你正在输入框里打字时也不动。
//
// ⚠ 风险：自动替你发消息，可能违反 ChatGPT / OpenAI 的使用条款，可能导致账号受限。默认关闭，打开等于你自己承担风险。
// 打开方法：把下面 ENABLED 那行的 false 改成 true，然后到 chrome://extensions 点这个扩展的“重新加载”，再刷新 ChatGPT 页面。
(() => {
  const ENABLED = false; // ← 打开时改这里
  if (!ENABLED || window.__proLaneAutoContinue) return;
  window.__proLaneAutoContinue = true;

  const IDLE_MS = 90_000;
  const MAX_PER_HOUR = 4;
  const MAX_PER_DAY_TAB = 20;
  const MAX_PER_DAY_ALL = 150;
  const MESSAGE = "继续。先 lane_checkin 签到，读你的 inbox.md 最新一节，接着干下一件；不写总结，不结束回复，做完一件马上做下一件。";
  const LANE_MARK = /lane_checkin|inbox\.md/;
  const LANES_KEY = "proLaneWorkLanes";
  const DAY_KEY = "proLaneContinueDaily";

  const today = () => new Date().toLocaleDateString("sv");
  const load = (key, fallback) => { try { return JSON.parse(localStorage.getItem(key)) ?? fallback; } catch (e) { return fallback; } };
  const save = (key, value) => { try { localStorage.setItem(key, JSON.stringify(value)); } catch (e) {} };

  function daily() {
    const d = load(DAY_KEY, null);
    return d && d.day === today() ? d : { day: today(), all: 0, byPath: {} };
  }
  function dailyAllowed() {
    const d = daily();
    return d.all < MAX_PER_DAY_ALL && (d.byPath[location.pathname] || 0) < MAX_PER_DAY_TAB;
  }
  function dailyCount() {
    const d = daily();
    d.all += 1;
    d.byPath[location.pathname] = (d.byPath[location.pathname] || 0) + 1;
    save(DAY_KEY, d);
    return d;
  }

  function isWorkLane() {
    const path = location.pathname;
    const lanes = load(LANES_KEY, []);
    let known = lanes.includes(path);
    if (!known && LANE_MARK.test(document.body.innerText || "")) {
      save(LANES_KEY, [...lanes, path].slice(-60));
      known = true;
    }
    if (!known) return false;
    const users = document.querySelectorAll('[data-message-author-role="user"]');
    const last = users[users.length - 1];
    return !(last && /【暂停】|【暫停】/.test(last.innerText || ""));
  }

  const generating = () => Boolean(document.querySelector('[data-testid="stop-button"], button[aria-label*="停止"], button[aria-label*="Stop"]'));
  const visible = (el) => el && el.offsetParent !== null;
  function composer() {
    const all = document.querySelectorAll('#prompt-textarea, div[contenteditable="true"][id*="prompt"], textarea[data-testid="prompt-textarea"]');
    return [...all].find(visible) || null;
  }
  const sendButton = () => document.querySelector('[data-testid="send-button"], button[aria-label*="发送"], button[aria-label*="Send"]');
  const textOf = (box) => (box.tagName === "TEXTAREA" ? box.value : box.innerText) || "";

  function badge(message) {
    let el = document.getElementById("pro-lane-badge");
    if (!el) {
      el = document.createElement("div");
      el.id = "pro-lane-badge";
      el.style.cssText = "position:fixed;left:50%;top:12px;transform:translateX(-50%);z-index:2147483647;padding:6px 12px;border-radius:8px;" +
        "background:rgba(7,90,231,.92);color:#fff;font:13px/1.4 -apple-system,sans-serif;pointer-events:none;transition:opacity .2s";
      document.documentElement.appendChild(el);
    }
    el.textContent = message;
    el.style.opacity = "1";
    clearTimeout(el.__hide);
    el.__hide = setTimeout(() => { el.style.opacity = "0"; }, 8000);
  }

  let lastChange = Date.now();
  const sent = [];

  function tryContinue() {
    const now = Date.now();
    if (now - lastChange < IDLE_MS || generating() || !isWorkLane()) return;
    while (sent.length && now - sent[0] > 3_600_000) sent.shift();
    if (sent.length >= MAX_PER_HOUR || !dailyAllowed()) return;
    const box = composer();
    if (!box || textOf(box).trim()) return; // 你正在打字就不动
    box.focus();
    if (box.tagName === "TEXTAREA") {
      box.value = MESSAGE;
      box.dispatchEvent(new Event("input", { bubbles: true }));
    } else {
      document.execCommand("insertText", false, MESSAGE);
    }
    setTimeout(() => {
      if (!textOf(box).includes("继续")) return; // 没打进去就不发，下一轮再试
      const button = sendButton();
      if (!button || button.disabled) return;
      button.click();
      sent.push(Date.now());
      const d = dailyCount();
      lastChange = Date.now();
      badge(`已自动发“继续”（这一小时第 ${sent.length}/${MAX_PER_HOUR} 次；今天这框 ${d.byPath[location.pathname]}/${MAX_PER_DAY_TAB}、全部 ${d.all}/${MAX_PER_DAY_ALL}）`);
    }, 600);
  }

  new MutationObserver(() => { lastChange = Date.now(); })
    .observe(document.querySelector("main") || document.body, { childList: true, subtree: true, characterData: true });
  setInterval(tryContinue, 20_000);
})();
