import logging  # 用于记录日志的模块
import sys  # 提供对Python解释器相关功能的访问

# 配置日志记录器，设置日志级别为INFO，并将日志输出到标准错误流
logging.basicConfig(level=logging.INFO, handlers=[logging.StreamHandler(sys.stderr)])

import argparse  # 用于解析命令行参数的模块
import asyncio  # 提供异步I/O支持的模块
import atexit  # 用于注册程序退出时执行的函数
import json  # 用于处理JSON数据的模块
from inspect import Parameter, Signature  # 用于获取函数参数和签名的模块
from typing import Any, Dict, Optional  # 用于类型注解的模块

from mcp.server.fastmcp import FastMCP  # 导入FastMCP类，用于实现MCP服务器

from app.logger import logger  # 导入自定义的日志记录器
from app.tool.base import BaseTool  # 导入工具基类
from app.tool.bash import Bash  # 导入Bash工具类
from app.tool.browser_use_tool import BrowserUseTool  # 导入浏览器工具类
from app.tool.str_replace_editor import StrReplaceEditor  # 导入字符串替换编辑器工具类
from app.tool.terminate import Terminate  # 导入终止工具类


class MCPServer:
    """
    MCP服务器实现类，用于注册和管理工具。

    参数:
        name (str): 服务器名称，默认为"openmanus"。
    """

    def __init__(self, name: str = "openmanus"):
        """
        初始化MCP服务器实例。

        参数:
            name (str): 服务器名称，默认为"openmanus"。
        """
        self.server = FastMCP(name)  # 创建FastMCP实例，用于底层通信
        self.tools: Dict[str, BaseTool] = {}  # 存储注册的工具，键为工具名称，值为工具实例

        # 初始化标准工具
        self.tools["bash"] = Bash()  # 注册Bash工具
        self.tools["browser"] = BrowserUseTool()  # 注册浏览器工具
        self.tools["editor"] = StrReplaceEditor()  # 注册字符串替换编辑器工具
        self.tools["terminate"] = Terminate()  # 注册终止工具

    def register_tool(self, tool: BaseTool, method_name: Optional[str] = None) -> None:
        """
        注册一个工具到MCP服务器，包括参数验证和文档生成。

        参数:
            tool (BaseTool): 需要注册的工具实例。
            method_name (Optional[str]): 工具的方法名称，如果未提供，则使用工具的名称。
        """
        tool_name = method_name or tool.name  # 确定工具的方法名称
        tool_param = tool.to_param()  # 获取工具的配置参数
        tool_function = tool_param["function"]  # 获取工具的功能定义

        # 定义异步函数，用于执行工具的实际操作
        async def tool_method(**kwargs):
            logger.info(f"Executing {tool_name}: {kwargs}")  # 记录工具执行日志
            result = await tool.execute(**kwargs)  # 执行工具操作

            logger.info(f"Result of {tool_name}: {result}")  # 记录工具执行结果

            # 处理不同类型的返回结果
            if hasattr(result, "model_dump"):  # 如果结果支持model_dump方法，转换为JSON字符串
                return json.dumps(result.model_dump())
            elif isinstance(result, dict):  # 如果结果是字典，转换为JSON字符串
                return json.dumps(result)
            return result  # 其他情况直接返回结果

        # 设置方法的元数据
        tool_method.__name__ = tool_name  # 设置方法名称
        tool_method.__doc__ = self._build_docstring(tool_function)  # 设置方法文档
        tool_method.__signature__ = self._build_signature(tool_function)  # 设置方法签名

        # 存储参数模式（用于工具程序化访问）
        param_props = tool_function.get("parameters", {}).get("properties", {})
        required_params = tool_function.get("parameters", {}).get("required", [])
        tool_method._parameter_schema = {
            param_name: {
                "description": param_details.get("description", ""),
                "type": param_details.get("type", "any"),
                "required": param_name in required_params,
            }
            for param_name, param_details in param_props.items()
        }

        # 将工具方法注册到服务器
        self.server.tool()(tool_method)
        logger.info(f"Registered tool: {tool_name}")  # 记录工具注册日志

    def _build_docstring(self, tool_function: dict) -> str:
        """
        根据工具的功能定义生成格式化的文档字符串。

        参数:
            tool_function (dict): 工具的功能定义，包含描述和参数信息。

        返回:
            str: 格式化后的文档字符串。
        """
        description = tool_function.get("description", "")  # 获取工具的描述
        param_props = tool_function.get("parameters", {}).get("properties", {})  # 获取工具的参数属性
        required_params = tool_function.get("parameters", {}).get("required", [])  # 获取必填参数列表

        # 构建文档字符串
        docstring = description
        if param_props:
            docstring += "\n\nParameters:\n"
            for param_name, param_details in param_props.items():
                required_str = (
                    "(required)" if param_name in required_params else "(optional)"
                )
                param_type = param_details.get("type", "any")  # 获取参数类型
                param_desc = param_details.get("description", "")  # 获取参数描述
                docstring += (
                    f"    {param_name} ({param_type}) {required_str}: {param_desc}\n"
                )

        return docstring

    def _build_signature(self, tool_function: dict) -> Signature:
        """
        根据工具的功能定义生成函数签名。

        参数:
            tool_function (dict): 工具的功能定义，包含参数信息。

        返回:
            Signature: 生成的函数签名对象。
        """
        param_props = tool_function.get("parameters", {}).get("properties", {})  # 获取工具的参数属性
        required_params = tool_function.get("parameters", {}).get("required", [])  # 获取必填参数列表

        parameters = []

        # 遍历参数属性，生成参数对象
        for param_name, param_details in param_props.items():
            param_type = param_details.get("type", "")  # 获取参数类型
            default = Parameter.empty if param_name in required_params else None  # 设置默认值

            # 根据JSON Schema类型映射到Python类型
            annotation = Any
            if param_type == "string":
                annotation = str
            elif param_type == "integer":
                annotation = int
            elif param_type == "number":
                annotation = float
            elif param_type == "boolean":
                annotation = bool
            elif param_type == "object":
                annotation = dict
            elif param_type == "array":
                annotation = list

            # 创建参数对象
            param = Parameter(
                name=param_name,
                kind=Parameter.KEYWORD_ONLY,
                default=default,
                annotation=annotation,
            )
            parameters.append(param)

        return Signature(parameters=parameters)

    async def cleanup(self) -> None:
        """
        清理服务器资源，主要用于关闭浏览器工具的资源。
        """
        logger.info("Cleaning up resources")  # 记录清理资源日志
        # 仅清理浏览器工具的资源（如果存在）
        if "browser" in self.tools and hasattr(self.tools["browser"], "cleanup"):
            await self.tools["browser"].cleanup()

    def register_all_tools(self) -> None:
        """
        注册所有已初始化的工具到服务器。
        """
        for tool in self.tools.values():  # 遍历所有工具
            self.register_tool(tool)  # 注册工具

    def run(self, transport: str = "stdio") -> None:
        """
        启动MCP服务器。

        参数:
            transport (str): 通信方式，默认为"stdio"。
        """
        # 注册所有工具
        self.register_all_tools()

        # 注册清理函数，在程序退出时执行
        atexit.register(lambda: asyncio.run(self.cleanup()))

        # 启动服务器
        logger.info(f"Starting OpenManus server ({transport} mode)")  # 记录服务器启动日志
        self.server.run(transport=transport)


def parse_args() -> argparse.Namespace:
    """
    解析命令行参数。

    返回:
        argparse.Namespace: 包含解析后的命令行参数的对象。
    """
    parser = argparse.ArgumentParser(description="OpenManus MCP Server")  # 创建参数解析器
    parser.add_argument(
        "--transport",
        choices=["stdio"],
        default="stdio",
        help="Communication method: stdio or http (default: stdio)",  # 添加transport参数
    )
    return parser.parse_args()  # 解析并返回参数


if __name__ == "__main__":
    args = parse_args()  # 解析命令行参数

    # 创建并运行服务器
    server = MCPServer()  # 创建MCPServer实例
    server.run(transport=args.transport)  # 启动服务器