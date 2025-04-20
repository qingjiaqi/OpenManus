from contextlib import AsyncExitStack
from typing import List, Optional

# 导入 MCP 相关的客户端和参数模块
from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.types import TextContent

# 导入应用日志和基础工具模块
from app.logger import logger
from app.tool.base import BaseTool, ToolResult
from app.tool.tool_collection import ToolCollection


class MCPClientTool(BaseTool):
    """
    表示一个可以从客户端调用的 MCP 服务器工具代理。
    """

    session: Optional[ClientSession] = None  # 当前与 MCP 服务器的会话

    async def execute(self, **kwargs) -> ToolResult:
        """
        通过远程调用 MCP 服务器执行工具。

        参数:
            **kwargs: 工具调用所需的参数。

        返回:
            ToolResult: 包含执行结果或错误信息的工具结果对象。
        """
        if not self.session:
            return ToolResult(error="Not connected to MCP server")

        try:
            # 调用远程工具
            result = await self.session.call_tool(self.name, kwargs)
            # 提取文本内容并拼接为字符串
            content_str = ", ".join(
                item.text for item in result.content if isinstance(item, TextContent)
            )
            return ToolResult(output=content_str or "No output returned.")
        except Exception as e:
            return ToolResult(error=f"Error executing tool: {str(e)}")


class MCPClients(ToolCollection):
    """
    一个工具集合，用于连接到 MCP 服务器并通过模型上下文协议管理可用工具。
    """

    session: Optional[ClientSession] = None  # 当前与 MCP 服务器的会话
    exit_stack: AsyncExitStack = None  # 异步上下文管理栈
    description: str = "MCP client tools for server interaction"  # 工具集合描述

    def __init__(self):
        super().__init__()  # 初始化空工具列表
        self.name = "mcp"  # 保留名称以向后兼容
        self.exit_stack = AsyncExitStack()  # 初始化异步上下文管理栈

    async def connect_sse(self, server_url: str) -> None:
        """
        使用 SSE 传输连接到 MCP 服务器。

        参数:
            server_url (str): MCP 服务器的 URL。

        异常:
            ValueError: 如果 server_url 为空。
        """
        if not server_url:
            raise ValueError("Server URL is required.")
        if self.session:
            await self.disconnect()

        # 创建 SSE 客户端连接
        streams_context = sse_client(url=server_url)
        streams = await self.exit_stack.enter_async_context(streams_context)
        self.session = await self.exit_stack.enter_async_context(
            ClientSession(*streams)
        )

        await self._initialize_and_list_tools()

    async def connect_stdio(self, command: str, args: List[str]) -> None:
        """
        使用 stdio 传输连接到 MCP 服务器。

        参数:
            command (str): 服务器启动命令。
            args (List[str]): 服务器启动参数。

        异常:
            ValueError: 如果 command 为空。
        """
        if not command:
            raise ValueError("Server command is required.")
        if self.session:
            await self.disconnect()

        # 创建 stdio 客户端连接
        server_params = StdioServerParameters(command=command, args=args)
        stdio_transport = await self.exit_stack.enter_async_context(
            stdio_client(server_params)
        )
        read, write = stdio_transport
        self.session = await self.exit_stack.enter_async_context(
            ClientSession(read, write)
        )

        await self._initialize_and_list_tools()

    async def _initialize_and_list_tools(self) -> None:
        """
        初始化会话并填充工具映射。

        异常:
            RuntimeError: 如果会话未初始化。
        """
        if not self.session:
            raise RuntimeError("Session not initialized.")

        # 初始化会话并列出可用工具
        await self.session.initialize()
        response = await self.session.list_tools()

        # 清空现有工具
        self.tools = tuple()
        self.tool_map = {}

        # 为每个服务器工具创建代理对象
        for tool in response.tools:
            server_tool = MCPClientTool(
                name=tool.name,
                description=tool.description,
                parameters=tool.inputSchema,
                session=self.session,
            )
            self.tool_map[tool.name] = server_tool

        self.tools = tuple(self.tool_map.values())
        logger.info(
            f"Connected to server with tools: {[tool.name for tool in response.tools]}"
        )

    async def disconnect(self) -> None:
        """
        断开与 MCP 服务器的连接并清理资源。
        """
        if self.session and self.exit_stack:
            await self.exit_stack.aclose()
            self.session = None
            self.tools = tuple()
            self.tool_map = {}
            logger.info("Disconnected from MCP server")
