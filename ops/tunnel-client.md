# 用官方隧道把服务接到 ChatGPT

官方文档：<https://developers.openai.com/api/docs/guides/secure-mcp-tunnels>（界面和命令以官方最新为准）

tunnel-client 跑在你的电脑上，主动用 HTTPS 往外连 OpenAI，取回排队的工具调用，转给本机服务。**你不用开任何公网端口。**

## 1. 建隧道
在 OpenAI 开发者平台的组织设置里找到 Tunnels，建一个隧道，记下 `tunnel_id`，并把它关联到你的 ChatGPT 工作区（只关联平台组织的话，ChatGPT 里看不到它）。再建一个运行用的 API key。

## 2. 跑起来
```bash
export CONTROL_PLANE_API_KEY=你的运行key   # 只放在本机，别写进项目，别发进聊天
tunnel-client init --profile mylane --tunnel-id 你的tunnel_id --mcp-server-url http://127.0.0.1:8765/mcp
tunnel-client doctor --profile mylane --explain
tunnel-client run --profile mylane
```
`doctor` 全绿再 `run`。这个进程要一直开着，建议和服务一样做成开机自启。

## 3. 在 ChatGPT 里加插件
设置 → 插件 → 添加自定义 MCP 服务器 → “连接”选**隧道**，选你的隧道 → 勾“我已了解” → 创建。
到插件的权限页，**先选“允许使用只读工具”**。

找不到添加入口：先在设置的高级选项里打开开发者模式。隧道不出现：检查它有没有关联到 ChatGPT 工作区。

## 官方限制（写在官方文档里）
- 单次请求 120 秒、每个隧道每分钟 600 次、单条消息 10 MB。
- 我们实测 ChatGPT 那边 60 秒左右就不太耐烦，所以前台命令定了 45 秒。
