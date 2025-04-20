from typing import Optional  # 导入Optional类型，用于可选参数的类型注解

from pydantic import Field, model_validator  # 导入Pydantic的Field和model_validator，用于数据验证和字段定义

from app.agent.browser import BrowserContextHelper  # 导入浏览器上下文助手类
from app.agent.toolcall import ToolCallAgent  # 导入工具调用代理基类
from app.config import config  # 导入配置文件
from app.prompt.manus import NEXT_STEP_PROMPT, SYSTEM_PROMPT  # 导入提示词模板
from app.tool import Terminate, ToolCollection  # 导入工具集合和终止工具
from app.tool.browser_use_tool import BrowserUseTool  # 导入浏览器使用工具
from app.tool.python_execute import PythonExecute  # 导入Python执行工具
from app.tool.str_replace_editor import StrReplaceEditor  # 导入字符串替换编辑器工具


class Manus(ToolCallAgent):
    """A versatile general-purpose agent."""  # Manus代理类，通用多功能代理

    name: str = "Manus"  # 代理名称
    description: str = (
        "A versatile agent that can solve various tasks using multiple tools"
    )  # 代理描述

    system_prompt: str = SYSTEM_PROMPT.format(directory=config.workspace_root)  # 系统提示词，格式化工作目录
    next_step_prompt: str = NEXT_STEP_PROMPT  # 下一步提示词

    max_observe: int = 10000  # 最大观察次数限制
    max_steps: int = 20  # 最大步骤限制

    # 添加通用工具到工具集合
    available_tools: ToolCollection = Field(
        default_factory=lambda: ToolCollection(
            PythonExecute(), BrowserUseTool(), StrReplaceEditor(), Terminate()
        )
    )

    special_tool_names: list[str] = Field(default_factory=lambda: [Terminate().name])  # 特殊工具名称列表

    browser_context_helper: Optional[BrowserContextHelper] = None  # 浏览器上下文助手，初始为None

    @model_validator(mode="after")
    def initialize_helper(self) -> "Manus":
        """初始化浏览器上下文助手"""
        self.browser_context_helper = BrowserContextHelper(self)  # 创建浏览器上下文助手实例
        return self

    async def think(self) -> bool:
        """处理当前状态并决定下一步操作"""
        original_prompt = self.next_step_prompt  # 保存原始提示词
        recent_messages = self.memory.messages[-3:] if self.memory.messages else []  # 获取最近3条消息
        browser_in_use = any(
            tc.function.name == BrowserUseTool().name
            for msg in recent_messages
            if msg.tool_calls
            for tc in msg.tool_calls
        )  # 检查是否正在使用浏览器工具

        if browser_in_use:
            self.next_step_prompt = (
                await self.browser_context_helper.format_next_step_prompt()
            )  # 如果使用浏览器工具，更新提示词

        result = await super().think()  # 调用父类的think方法

        # 恢复原始提示词
        self.next_step_prompt = original_prompt

        return result

    async def cleanup(self):
        """清理Manus代理的资源"""
        if self.browser_context_helper:
            await self.browser_context_helper.cleanup_browser()  # 清理浏览器资源