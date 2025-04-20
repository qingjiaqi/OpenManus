#!/usr/bin/env python
# 使用Python解释器运行此脚本

import argparse
# 用于解析命令行参数
import asyncio
# 异步IO库，用于支持异步编程
import sys
# 系统相关功能，如退出程序

from app.agent.mcp import MCPAgent
# 导入MCPAgent类，用于与MCP服务器交互
from app.config import config
# 导入配置文件
from app.logger import logger
# 导入日志记录器


class MCPRunner:
    """
    MCPAgent的运行器类，负责路径处理和配置管理。
    """

    def __init__(self):
        """
        初始化MCPRunner实例。
        - root_path: 根路径，从配置中获取
        - server_reference: 服务器引用，从配置中获取
        - agent: MCPAgent实例
        """
        self.root_path = config.root_path
        self.server_reference = config.mcp_config.server_reference
        self.agent = MCPAgent()

    async def initialize(
        self,
        connection_type: str,
        server_url: str | None = None,
    ) -> None:
        """
        初始化MCPAgent，根据连接类型设置连接方式。
        - connection_type: 连接类型（stdio或sse）
        - server_url: 仅当connection_type为sse时使用，指定服务器URL
        """
        logger.info(f"Initializing MCPAgent with {connection_type} connection...")

        if connection_type == "stdio":
            # 使用标准输入输出连接
            await self.agent.initialize(
                connection_type="stdio",
                command=sys.executable,
                args=["-m", self.server_reference],
            )
        else:  # sse
            # 使用SSE连接
            await self.agent.initialize(connection_type="sse", server_url=server_url)

        logger.info(f"Connected to MCP server via {connection_type}")

    async def run_interactive(self) -> None:
        """
        以交互模式运行MCPAgent。
        - 用户输入请求，直到输入退出命令
        """
        print("\nMCP Agent Interactive Mode (type 'exit' to quit)\n")
        while True:
            user_input = input("\nEnter your request: ")
            if user_input.lower() in ["exit", "quit", "q"]:
                break
            response = await self.agent.run(user_input)
            print(f"\nAgent: {response}")

    async def run_single_prompt(self, prompt: str) -> None:
        """
        运行MCPAgent处理单个提示。
        - prompt: 用户输入的提示
        """
        await self.agent.run(prompt)

    async def run_default(self) -> None:
        """
        以默认模式运行MCPAgent。
        - 提示用户输入，处理请求
        """
        prompt = input("Enter your prompt: ")
        if not prompt.strip():
            logger.warning("Empty prompt provided.")
            return

        logger.warning("Processing your request...")
        await self.agent.run(prompt)
        logger.info("Request processing completed.")

    async def cleanup(self) -> None:
        """
        清理MCPAgent资源。
        """
        await self.agent.cleanup()
        logger.info("Session ended")


def parse_args() -> argparse.Namespace:
    """
    解析命令行参数。
    返回:
        argparse.Namespace: 解析后的命令行参数
    """
    parser = argparse.ArgumentParser(description="Run the MCP Agent")
    parser.add_argument(
        "--connection",
        "-c",
        choices=["stdio", "sse"],
        default="stdio",
        help="Connection type: stdio or sse",
    )
    parser.add_argument(
        "--server-url",
        default="http://127.0.0.1:8000/sse",
        help="URL for SSE connection",
    )
    parser.add_argument(
        "--interactive", "-i", action="store_true", help="Run in interactive mode"
    )
    parser.add_argument("--prompt", "-p", help="Single prompt to execute and exit")
    return parser.parse_args()


async def run_mcp() -> None:
    """
    MCP运行器的主入口函数。
    - 解析命令行参数
    - 初始化MCPRunner
    - 根据参数运行相应模式
    """
    args = parse_args()
    runner = MCPRunner()

    try:
        await runner.initialize(args.connection, args.server_url)

        if args.prompt:
            await runner.run_single_prompt(args.prompt)
        elif args.interactive:
            await runner.run_interactive()
        else:
            await runner.run_default()

    except KeyboardInterrupt:
        logger.info("Program interrupted by user")
    except Exception as e:
        logger.error(f"Error running MCPAgent: {str(e)}", exc_info=True)
        sys.exit(1)
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    # 异步运行主函数
    asyncio.run(run_mcp())
