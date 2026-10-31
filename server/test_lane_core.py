"""lane_core 自测：python3 -m unittest test_lane_core.py（只用标准库）。"""
import os
import tempfile
import time
import unittest
from pathlib import Path

from lane_core import Lane, LaneError


class LaneTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.proj = base / "proj"
        self.proj.mkdir()
        (self.proj / "a.txt").write_text("hello", encoding="utf-8")
        (self.proj / ".env").write_text("TOKEN=x", encoding="utf-8")
        (base / "outside.txt").write_text("nope", encoding="utf-8")
        self.base = base

    def tearDown(self):
        self.tmp.cleanup()

    def lane(self, **kw):
        return Lane({"roots": [str(self.proj)], **kw}, self.base)

    def test_read_only_by_default(self):
        lane = self.lane()
        self.assertEqual(lane.read_file("a.txt")["text"], "hello")
        with self.assertRaises(LaneError):
            lane.write_file("b.txt", "x")
        with self.assertRaises(LaneError):
            lane.run_command("echo hi")

    def test_fence(self):
        lane = self.lane()
        for bad in ("../outside.txt", str(self.base / "outside.txt"), ".env"):
            with self.assertRaises(LaneError, msg=bad):
                lane.read_file(bad)
        os.symlink(self.base / "outside.txt", self.proj / "link.txt")
        with self.assertRaises(LaneError):
            lane.read_file("link.txt")
        names = [i["name"] for i in lane.list_dir(".")]
        self.assertIn("a.txt", names)
        self.assertNotIn(".env", names)

    def test_write_with_sha(self):
        lane = self.lane(allow_write=True)
        r = lane.write_file("sub/b.txt", "v1")
        lane.write_file("sub/b.txt", "v2", expected_sha256=r["sha256"])
        with self.assertRaises(LaneError):
            lane.write_file("sub/b.txt", "v3", expected_sha256=r["sha256"])
        self.assertEqual((self.proj / "sub/b.txt").read_text(), "v2")

    def test_shell_timeout_and_compact(self):
        lane = self.lane(allow_shell=True, shell_timeout=1, max_output_chars=200)
        self.assertEqual(lane.run_command("echo hi")["output"].strip(), "hi")
        with self.assertRaises(LaneError):
            lane.run_command("sleep 3")
        out = lane.run_command("python3 -c \"print('x'*5000)\"")["output"]
        self.assertIn("中间省略", out)
        self.assertLess(len(out), 300)

    def test_background_job(self):
        lane = self.lane(allow_shell=True)
        job = lane.start_command("echo start; sleep 1; echo end")
        r = lane.get_command(job["job_id"], wait_seconds=10)
        self.assertEqual(r["state"], "finished")
        self.assertEqual(r["exit_code"], 0)
        self.assertIn("end", r["log_tail"])

    def test_checkin(self):
        lane = self.lane()
        d = lane.lanes_dir / "demo"
        d.mkdir(parents=True)
        (d / "inbox.md").write_text("# 旧的一节\n别做这个\n\n# 最新一节\n先修登录页\n", encoding="utf-8")
        (d / "backlog.csv").write_text("id,status,task\n1,完成,旧活\n2,待处理,修登录页\n3,待处理,补测试\n4,待处理,写文档\n5,待处理,第五件\n", encoding="utf-8")
        first = lane.checkin("demo", first=True)
        self.assertIn("最新一节", first)
        self.assertNotIn("别做这个", first)
        self.assertIn("2. 修登录页", first)
        self.assertIn("4. 写文档", first)
        self.assertNotIn("第五件", first)
        lane.checkin("demo", done="修好登录页｜src/login.ts", next_item="补测试")
        again = lane.checkin("demo", first=True)
        self.assertIn("下一件：补测试", again)
        with self.assertRaises(LaneError):
            lane.checkin("../x")


if __name__ == "__main__":
    unittest.main()
