"""pro-lane-kit 核心逻辑：只用 Python 标准库，可以脱离 MCP 单独测试。

安全默认：
- 只能访问 config 里 roots 列出的目录；符号链接跳出去也会被拦。
- 密钥类文件（.ssh、.env、*.pem、*.key……）一律拒读拒写。
- 写文件、跑命令两个开关默认关（allow_write / allow_shell）。
- 前台命令 45 秒封顶；超过 30 秒的活请用后台任务。
- 审计日志只记时间、工具名、结果，不记参数和内容。
"""
from __future__ import annotations

import csv
import fnmatch
import hashlib
import json
import os
import re
import signal
import subprocess
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

DEFAULTS = {
    "roots": [],
    "allow_write": False,
    "allow_shell": False,
    "lanes_dir": "./lanes",
    "jobs_dir": "./.jobs",
    "audit_log": "./audit.jsonl",
    "shell_timeout": 45,          # 前台命令秒数上限。ChatGPT 侧大约 60 秒就不耐烦，官方隧道单次上限 120 秒
    "job_timeout": 3600,          # 后台任务最长跑多久
    "job_log_max_bytes": 8 * 1024 * 1024,
    "max_jobs": 4,                # 同时跑的后台任务数
    "max_read_bytes": 200_000,
    "max_output_chars": 12_000,   # 回给模型的文字上限：太长掐头留尾，对话才活得久
    "deny_globs": [
        ".ssh/*", "*/.ssh/*", ".env", "*/.env", ".env.*", "*/.env.*", "*.pem", "*.key", "*.p12",
        "id_rsa*", "*/id_rsa*", "id_ed25519*", "*/id_ed25519*", ".aws/*", "*/.aws/*",
        ".git/config", "*/.git/config", "*secret*", "*token*", "*credential*", "*password*",
    ],
}

LANE_NAME = re.compile(r"^[\w\-一-鿿]{1,64}$")
RULES = ("做完一件签到一次（done 写：做了什么｜东西在哪｜下一件），马上接下一件；"
         "不写总结、不结束回复、不问要不要继续；超过 30 秒的命令用 start_command 放后台、用 get_command 等结果；"
         "真卡住才写「卡住：缺什么」，然后先做下一件能做的。")


class LaneError(Exception):
    """给模型看的、可读的错误。"""


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def compact(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    head = limit // 4
    tail = limit - head
    return f"{text[:head]}\n…（中间省略 {len(text) - limit} 字）…\n{text[-tail:]}"


class Lane:
    def __init__(self, config: dict, base: Path):
        cfg = {**DEFAULTS, **config}
        self.cfg = cfg
        self.base = Path(base).resolve()
        self.roots = [self._abs(r) for r in cfg["roots"]]
        if not self.roots:
            raise LaneError("config 里 roots 不能为空：至少写一个项目目录")
        self.allow_write = bool(cfg["allow_write"])
        self.allow_shell = bool(cfg["allow_shell"])
        self.lanes_dir = self._abs(cfg["lanes_dir"])
        self.jobs_dir = self._abs(cfg["jobs_dir"])
        self.audit_path = self._abs(cfg["audit_log"])
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self._jobs: dict[str, subprocess.Popen] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------ 基础
    def _abs(self, p: str) -> Path:
        q = Path(os.path.expanduser(p))
        return (q if q.is_absolute() else self.base / q).resolve()

    def audit(self, tool: str, status: str) -> None:
        line = json.dumps({"at": now(), "tool": tool, "status": status}, ensure_ascii=False)
        with self._lock, open(self.audit_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def resolve(self, path: str) -> Path:
        """把模型给的路径变成 roots 里的真实路径；跳出 roots 或碰到密钥类文件就拒绝。"""
        raw = Path(os.path.expanduser(path or "."))
        p = (raw if raw.is_absolute() else self.roots[0] / raw).resolve()
        root = next((r for r in self.roots if p == r or r in p.parents), None)
        if root is None:
            raise LaneError(f"路径不在允许的目录里：{path}")
        rel = p.relative_to(root).as_posix() if p != root else "."
        for pat in self.cfg["deny_globs"]:
            if fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(p.name, pat):
                raise LaneError(f"这个文件按规则不准碰（{pat}）：{path}")
        return p

    def _need(self, flag: bool, name: str) -> None:
        if not flag:
            raise LaneError(f"{name} 没打开：要用请在 config.json 里把它设成 true 并重启服务（默认只读）")

    # ------------------------------------------------------------ 读
    def read_file(self, path: str, offset: int = 0, max_bytes: int | None = None) -> dict:
        p = self.resolve(path)
        if not p.is_file():
            raise LaneError(f"不是文件或不存在：{path}")
        limit = min(int(max_bytes or self.cfg["max_read_bytes"]), self.cfg["max_read_bytes"])
        size = p.stat().st_size
        with open(p, "rb") as f:
            f.seek(max(0, int(offset)))
            data = f.read(limit)
        text = data.decode("utf-8", errors="replace")
        return {"path": str(p), "size": size, "offset": offset, "truncated": offset + len(data) < size,
                "sha256": hashlib.sha256(p.read_bytes()).hexdigest() if size <= 20_000_000 else None, "text": text}

    def read_bundle(self, paths: list[str], max_bytes_each: int = 40_000) -> list[dict]:
        out = []
        for path in paths[:30]:
            try:
                r = self.read_file(path, 0, max_bytes_each)
                out.append({"path": r["path"], "truncated": r["truncated"], "text": r["text"]})
            except LaneError as e:
                out.append({"path": path, "error": str(e)})
        return out

    def list_dir(self, path: str = ".", max_entries: int = 300) -> list[dict]:
        p = self.resolve(path)
        if not p.is_dir():
            raise LaneError(f"不是目录：{path}")
        items = []
        for child in sorted(p.iterdir(), key=lambda c: (not c.is_dir(), c.name))[:max_entries]:
            try:
                self.resolve(str(child))
            except LaneError:
                continue  # 密钥类文件连名字都不列
            items.append({"name": child.name, "type": "dir" if child.is_dir() else "file",
                          "size": child.stat().st_size if child.is_file() else None})
        return items

    # ------------------------------------------------------------ 写（默认关）
    def write_file(self, path: str, content: str, expected_sha256: str | None = None) -> dict:
        self._need(self.allow_write, "allow_write（写文件）")
        p = self.resolve(path)
        if expected_sha256 and p.exists() and hashlib.sha256(p.read_bytes()).hexdigest() != expected_sha256:
            raise LaneError("文件已经被别人改过（sha256 对不上），先重新读再写")
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(f".{p.name}.{uuid.uuid4().hex[:8]}.tmp")
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, p)
        return {"path": str(p), "bytes": p.stat().st_size, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}

    # ------------------------------------------------------------ 命令（默认关）
    def run_command(self, command: str, cwd: str = ".") -> dict:
        self._need(self.allow_shell, "allow_shell（跑命令）")
        d = self.resolve(cwd)
        t = int(self.cfg["shell_timeout"])
        try:
            r = subprocess.run(command, shell=True, cwd=d, capture_output=True, text=True, timeout=t, errors="replace")
        except subprocess.TimeoutExpired:
            raise LaneError(f"命令超过 {t} 秒被停掉。长命令请用 start_command 放后台，再用 get_command 取结果")
        out = (r.stdout or "") + (("\n[stderr]\n" + r.stderr) if r.stderr else "")
        return {"exit_code": r.returncode, "output": compact(out, self.cfg["max_output_chars"])}

    def start_command(self, command: str, cwd: str = ".") -> dict:
        self._need(self.allow_shell, "allow_shell（跑命令）")
        d = self.resolve(cwd)
        running = [j for j, p in self._jobs.items() if p.poll() is None]
        if len(running) >= int(self.cfg["max_jobs"]):
            raise LaneError(f"后台已经有 {len(running)} 个任务在跑（上限 {self.cfg['max_jobs']}），先 get_command 等一个跑完")
        job_id = datetime.now().strftime("%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        log = self.jobs_dir / f"{job_id}.log"
        meta = {"job_id": job_id, "command": command, "cwd": str(d), "started": now()}
        (self.jobs_dir / f"{job_id}.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        f = open(log, "wb")
        proc = subprocess.Popen(command, shell=True, cwd=d, stdout=f, stderr=subprocess.STDOUT, start_new_session=True)
        f.close()
        self._jobs[job_id] = proc
        threading.Thread(target=self._watch, args=(job_id, proc, log), daemon=True).start()
        return {"job_id": job_id, "state": "running", "tip": "过一会儿用 get_command(job_id, wait_seconds=25) 取结果"}

    def _watch(self, job_id: str, proc: subprocess.Popen, log: Path) -> None:
        start = time.time()
        while proc.poll() is None:
            too_long = time.time() - start > int(self.cfg["job_timeout"])
            too_big = log.exists() and log.stat().st_size > int(self.cfg["job_log_max_bytes"])
            if too_long or too_big:
                with open(log, "a", encoding="utf-8") as f:
                    f.write(f"\n[pro-lane-kit] 任务被停掉：{'超时' if too_long else '日志超过上限'}\n")
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                except OSError:
                    proc.terminate()
                break
            time.sleep(2)

    def get_command(self, job_id: str, wait_seconds: int = 0) -> dict:
        if not re.fullmatch(r"[\w\-]{1,40}", job_id or ""):
            raise LaneError("job_id 不对")
        log = self.jobs_dir / f"{job_id}.log"
        if not log.exists():
            raise LaneError(f"找不到任务：{job_id}")
        proc = self._jobs.get(job_id)
        deadline = time.time() + max(0, min(int(wait_seconds), 25))  # 一次最多等 25 秒，保证在调用时限内回话
        while proc and proc.poll() is None and time.time() < deadline:
            time.sleep(0.5)
        state = "unknown（服务重启过）" if proc is None else ("running" if proc.poll() is None else "finished")
        text = log.read_bytes()[-200_000:].decode("utf-8", errors="replace")
        return {"job_id": job_id, "state": state, "exit_code": None if proc is None else proc.poll(),
                "log_tail": compact(text, self.cfg["max_output_chars"])}

    # ------------------------------------------------------------ 签到派活
    def _lane_dir(self, lane: str) -> Path:
        if not LANE_NAME.fullmatch(lane or ""):
            raise LaneError("车道名只能用中英文、数字、下划线、横线，最长 64 字")
        d = self.lanes_dir / lane
        d.mkdir(parents=True, exist_ok=True)
        return d

    def checkin(self, lane: str, first: bool = False, done: str = "", next_item: str = "") -> str:
        d = self._lane_dir(lane)
        log = d / "checkins.md"
        line = f"- {now()}｜{'开工' if first else '签到'}｜做了：{done.strip() or '—'}｜下一件：{next_item.strip() or '—'}\n"
        with self._lock, open(log, "a", encoding="utf-8") as f:
            f.write(line)
        parts = [f"【签到】{lane} {now()}"]
        if first:
            tail = log.read_text(encoding="utf-8").splitlines()[-11:-1]
            parts.append("【你上一班最后 10 行签到，从“下一件”接着干，不重做】\n" + ("\n".join(tail) if tail else "（没有，第一次开工）"))
        inbox = d / "inbox.md"
        if inbox.exists():
            sections = re.split(r"(?m)^(?=# )", inbox.read_text(encoding="utf-8"))
            latest = [s for s in sections if s.strip()]
            parts.append("【信箱最新一节，以它为准】\n" + (compact(latest[-1].strip(), 4000) if latest else "（空）"))
        backlog = d / "backlog.csv"
        if backlog.exists():
            with open(backlog, encoding="utf-8", newline="") as f:
                todo = [r for r in csv.DictReader(f) if (r.get("status") or "").strip() in ("", "待处理", "todo")][:3]
            parts.append("【清单接下来 3 件，现在就开始，不写总结】\n" + ("\n".join(f"{r.get('id', '?')}. {r.get('task', '')}" for r in todo) or "（清单空了：按信箱最新一节的活源自己找下一件）"))
        parts.append("【规矩】" + RULES)
        return "\n\n".join(parts)
