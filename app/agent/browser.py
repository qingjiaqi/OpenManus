import json
from typing import TYPE_CHECKING, Optional

from pydantic import Field, model_validator

from app.agent.toolcall import ToolCallAgent
from app.logger import logger
from app.prompt.browser import NEXT_STEP_PROMPT, SYSTEM_PROMPT
from app.schema import Message, ToolChoice
from app.tool import BrowserUseTool, Terminate, ToolCollection


# 避免循环导入，仅在类型检查时引入
if TYPE_CHECKING:
    from app.agent.base import BaseAgent  # 定义内存的基类


class BrowserContextHelper:
    """
    浏览器上下文助手类，用于管理浏览器状态和格式化提示信息。
    """

    def __init__(self, agent: "BaseAgent"):
        # 初始化浏览器上下文助手
        self.agent = agent  # 关联的代理实例
        self._current_base64_image: Optional[str] = None  # 当前浏览器截图（Base64格式）

    async def get_browser_state(self) -> Optional[dict]:
        """
        获取当前浏览器状态。
        返回值:
            Optional[dict]: 浏览器状态字典，包含URL、标题、标签页等信息。
        """
        # 获取浏览器工具实例
        browser_tool = self.agent.available_tools.get_tool(BrowserUseTool().name)
        if not browser_tool or not hasattr(browser_tool, "get_current_state"):
            logger.warning("BrowserUseTool未找到或不支持get_current_state")
            return None
        try:
            # 调用工具获取浏览器状态
            result = await browser_tool.get_current_state()
            if result.error:
                logger.debug(f"浏览器状态错误: {result.error}")
                return None
            # 更新当前截图
            if hasattr(result, "base64_image") and result.base64_image:
                self._current_base64_image = result.base64_image
            else:
                self._current_base64_image = None
            return json.loads(result.output)  # 解析JSON格式的状态数据
        except Exception as e:
            logger.debug(f"获取浏览器状态失败: {str(e)}")
            return None

    async def format_next_step_prompt(self) -> str:
        """
        格式化下一步提示信息，包含浏览器状态。
        返回值:
            str: 格式化后的提示字符串。
        """
        browser_state = await self.get_browser_state()
        url_info, tabs_info, content_above_info, content_below_info = "", "", "", ""
        results_info = ""  # 预留结果信息字段

        if browser_state and not browser_state.get("error"):
            # 提取URL和标题信息
            url_info = f"\n   URL: {browser_state.get('url', 'N/A')}\n   Title: {browser_state.get('title', 'N/A')}"
            tabs = browser_state.get("tabs", [])
            if tabs:
                tabs_info = f"\n   {len(tabs)} tab(s) available"
            # 提取页面滚动信息
            pixels_above = browser_state.get("pixels_above", 0)
            pixels_below = browser_state.get("pixels_below", 0)
            if pixels_above > 0:
                content_above_info = f" ({pixels_above} pixels)"
            if pixels_below > 0:
                content_below_info = f" ({pixels_below} pixels)"

            # 处理当前截图
            if self._current_base64_image:
                image_message = Message.user_message(
                    content="Current browser screenshot:",
                    base64_image=self._current_base64_image,
                )
                self.agent.memory.add_message(image_message)
                self._current_base64_image = None  # 使用后清空截图

        return NEXT_STEP_PROMPT.format(
            url_placeholder=url_info,
            tabs_placeholder=tabs_info,
            content_above_placeholder=content_above_info,
            content_below_placeholder=content_below_info,
            results_placeholder=results_info,
        )

    async def cleanup_browser(self):
        """清理浏览器资源"""
        browser_tool = self.agent.available_tools.get_tool(BrowserUseTool().name)
        if browser_tool and hasattr(browser_tool, "cleanup"):
            await browser_tool.cleanup()


class BrowserAgent(ToolCallAgent):
    """
    浏览器代理类，用于控制浏览器完成任务。
    功能包括导航、交互、表单填写、内容提取等。
    """

    name: str = "browser"
    description: str = "A browser agent that can control a browser to accomplish tasks"

    system_prompt: str = SYSTEM_PROMPT  # 系统提示模板
    next_step_prompt: str = NEXT_STEP_PROMPT  # 下一步提示模板

    max_observe: int = 10000  # 最大观察次数
    max_steps: int = 20  # 最大步骤数

    # 配置可用工具
    available_tools: ToolCollection = Field(
        default_factory=lambda: ToolCollection(BrowserUseTool(), Terminate())
    )

    # 工具选择模式（自动）
    tool_choices: ToolChoice = ToolChoice.AUTO
    special_tool_names: list[str] = Field(default_factory=lambda: [Terminate().name])

    browser_context_helper: Optional[BrowserContextHelper] = None  # 浏览器上下文助手

    @model_validator(mode="after")
    def initialize_helper(self) -> "BrowserAgent":
        """初始化浏览器上下文助手"""
        self.browser_context_helper = BrowserContextHelper(self)
        return self

    async def think(self) -> bool:
        """
        处理当前状态并决定下一步操作，同时更新浏览器状态提示。
        返回值:
            bool: 是否继续执行下一步。
        """
        self.next_step_prompt = (
            await self.browser_context_helper.format_next_step_prompt()
        )
        return await super().think()

    async def cleanup(self):
        """清理代理资源"""
        await self.browser_context_helper.cleanup_browser()
