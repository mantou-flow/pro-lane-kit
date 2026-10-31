"""pro-lane-kit：给网页版 ChatGPT 用的最小协作 MCP 服务。

用法：
    pip install -r requirements.txt
    cp config.example.json config.json   # 改 roots
    python pro_lane_server.py --config config.json

它只监听 127.0.0.1，地址是 http://127.0.0.1:8765/mcp。
再用 OpenAI 官方 tunnel-client 把这个地址接到 ChatGPT（见 ../ops/tunnel-client.md）。
写文件、跑命令两个开关默认关；关着的时候，ChatGPT 根本看不到这几个工具。
"""
import argparse
import json
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from lane_core import Lane, LaneError

READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=False)


def build(config_path: Path) -> FastMCP:
    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    core = Lane(cfg, config_path.parent)
    mcp = FastMCP("pro-lane-kit", host="127.0.0.1", port=int(cfg.get("port", 8765)))

    def call(tool, fn, *args):
        try:
            result = fn(*args)
            core.audit(tool, "ok")
            return result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, indent=1)
        except LaneError as e:
            core.audit(tool, "refused")
            return f"拒绝：{e}"
        except Exception as e:  # 不把内部堆栈丢给模型
            core.audit(tool, "error")
            return f"出错：{type(e).__name__}: {e}"

    # 签到只往本车道的 checkins.md 追加一行，不碰项目文件，所以按只读工具登记，免得每次签到都弹确认框。
    @mcp.tool(annotations=READ)
    def lane_checkin(lane: str, first: bool = False, done: str = "", next_item: str = "") -> str:
        """签到并领活。开工第一句先调用（first=true）；之后每做完一件调用一次，done 写“做了什么｜东西在哪”，next_item 写下一件。
        返回：信箱最新一节（以它为准）、清单接下来 3 件、规矩。读完马上开工，不写总结，不结束回复。"""
        return call("lane_checkin", core.checkin, lane, first, done, next_item)

    @mcp.tool(annotations=READ)
    def read_file(path: str, offset: int = 0, max_bytes: int = 200000) -> str:
        """读一个文件（相对路径从第一个项目目录算）。大文件用 offset 分段读。"""
        return call("read_file", core.read_file, path, offset, max_bytes)

    @mcp.tool(annotations=READ)
    def read_bundle(paths: list[str], max_bytes_each: int = 40000) -> str:
        """一次读多个文件（最多 30 个）。要看好几个文件时优先用它，别一个一个读。"""
        return call("read_bundle", core.read_bundle, paths, max_bytes_each)

    @mcp.tool(annotations=READ)
    def list_dir(path: str = ".") -> str:
        """列目录。"""
        return call("list_dir", core.list_dir, path)

    if core.allow_write:
        @mcp.tool(annotations=WRITE)
        def write_file(path: str, content: str, expected_sha256: str = "") -> str:
            """写文件（整份覆盖，原子写入）。改已有文件时带上读到的 sha256，防止覆盖别人的改动。"""
            return call("write_file", core.write_file, path, content, expected_sha256 or None)

    if core.allow_shell:
        @mcp.tool(annotations=WRITE)
        def run_command(command: str, cwd: str = ".") -> str:
            """跑一条短命令，45 秒封顶。超过 30 秒的活用 start_command。"""
            return call("run_command", core.run_command, command, cwd)

        @mcp.tool(annotations=WRITE)
        def start_command(command: str, cwd: str = ".") -> str:
            """把长命令（构建、测试）放后台跑，马上返回 job_id。"""
            return call("start_command", core.start_command, command, cwd)

        @mcp.tool(annotations=READ)
        def get_command(job_id: str, wait_seconds: int = 25) -> str:
            """取后台任务的状态和日志末尾；wait_seconds 最多 25 秒。"""
            return call("get_command", core.get_command, job_id, wait_seconds)

    return mcp


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(Path(__file__).with_name("config.json")))
    args = ap.parse_args()
    build(Path(args.config)).run(transport="streamable-http")
