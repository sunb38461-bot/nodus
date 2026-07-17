"""
MCP stdio 入口 —— 供只支持 stdio 的第三方客户端使用（如 Claude Desktop 旧版）

用法：在 MCP 配置中指向这个脚本，并通过环境变量传入 token
  "command": "python",
  "args": ["h:/ai-agent/nodus/mcp_stdio.py"],
  "env": {
    "NODUS_AGENT_TOKEN": "你的token"
  }
"""
import asyncio
import sys
import os

# 确保能导入 app 模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _show_usage_and_exit():
    print(
        "错误：未设置 NODUS_AGENT_TOKEN 环境变量。\n"
        "请在 MCP 客户端配置中设置 env.NODUS_AGENT_TOKEN，"
        "或在命令行通过 --token 参数传入。",
        file=sys.stderr,
    )
    sys.exit(1)


async def run():
    token = os.getenv("NODUS_AGENT_TOKEN")
    if not token and "--token" in sys.argv:
        idx = sys.argv.index("--token")
        if idx + 1 < len(sys.argv):
            token = sys.argv[idx + 1]
    if not token:
        _show_usage_and_exit()

    os.environ["NODUS_AGENT_TOKEN"] = token
    from app.mcp_tools import mcp
    await mcp.run_stdio_async()


if __name__ == "__main__":
    asyncio.run(run())
