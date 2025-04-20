# 导入必要的模块和类
from typing import Any, Dict, List

from app.exceptions import ToolError
from app.tool.base import BaseTool, ToolFailure, ToolResult


# 工具集合类，用于管理多个工具
class ToolCollection:
    """
    工具集合类，用于管理多个工具。
    """

    # 配置类，允许任意类型
    class Config:
        arbitrary_types_allowed = True

    # 初始化方法，接收多个工具并存储
    def __init__(self, *tools: BaseTool):
        """
        初始化工具集合。
        :param tools: 可变参数，接收多个工具实例。
        """
        self.tools = tools  # 存储工具列表
        self.tool_map = {tool.name: tool for tool in tools}  # 工具名称到工具的映射

    # 支持迭代工具集合
    def __iter__(self):
        """
        返回工具集合的迭代器。
        :return: 工具集合的迭代器。
        """
        return iter(self.tools)

    # 将工具集合转换为参数列表
    def to_params(self) -> List[Dict[str, Any]]:
        """
        将工具集合转换为参数列表。
        :return: 工具的参数列表。
        """
        return [tool.to_param() for tool in self.tools]

    # 执行指定名称的工具
    async def execute(self, *, name: str, tool_input: Dict[str, Any] = None) -> ToolResult:
        """
        执行指定名称的工具。
        :param name: 工具名称。
        :param tool_input: 工具的输入参数。
        :return: 工具执行结果。
        """
        tool = self.tool_map.get(name)  # 获取工具实例
        if not tool:
            return ToolFailure(error=f"Tool {name} is invalid")  # 工具不存在时返回失败
        try:
            result = await tool(**tool_input)  # 执行工具
            return result
        except ToolError as e:
            return ToolFailure(error=e.message)  # 捕获异常并返回失败

    # 依次执行所有工具
    async def execute_all(self) -> List[ToolResult]:
        """
        依次执行所有工具。
        :return: 所有工具的执行结果列表。
        """
        results = []
        for tool in self.tools:
            try:
                result = await tool()  # 执行工具
                results.append(result)
            except ToolError as e:
                results.append(ToolFailure(error=e.message))  # 捕获异常并记录失败
        return results

    # 获取指定名称的工具
    def get_tool(self, name: str) -> BaseTool:
        """
        获取指定名称的工具。
        :param name: 工具名称。
        :return: 工具实例，如果不存在则返回 None。
        """
        return self.tool_map.get(name)

    # 向集合中添加单个工具
    def add_tool(self, tool: BaseTool):
        """
        向集合中添加单个工具。
        :param tool: 工具实例。
        :return: 返回当前工具集合实例（支持链式调用）。
        """
        self.tools += (tool,)  # 添加工具到列表
        self.tool_map[tool.name] = tool  # 更新映射
        return self

    # 向集合中添加多个工具
    def add_tools(self, *tools: BaseTool):
        """
        向集合中添加多个工具。
        :param tools: 可变参数，接收多个工具实例。
        :return: 返回当前工具集合实例（支持链式调用）。
        """
        for tool in tools:
            self.add_tool(tool)  # 依次添加工具
        return self
