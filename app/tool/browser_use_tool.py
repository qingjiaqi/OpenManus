# 导入异步编程库
import asyncio
# 导入Base64编码库
import base64
# 导入JSON处理库
import json
# 导入类型提示相关模块
from typing import Generic, Optional, TypeVar

# 导入浏览器自动化工具相关模块
from browser_use import Browser as BrowserUseBrowser
from browser_use import BrowserConfig
from browser_use.browser.context import BrowserContext, BrowserContextConfig
from browser_use.dom.service import DomService
# 导入Pydantic用于数据验证
from pydantic import Field, field_validator
from pydantic_core.core_schema import ValidationInfo

# 导入项目配置和LLM模块
from app.config import config
from app.llm import LLM
# 导入基础工具类和工具结果类
from app.tool.base import BaseTool, ToolResult
from app.tool.web_search import WebSearch


# 浏览器工具的描述信息
_BROWSER_DESCRIPTION = """\
一个强大的浏览器自动化工具，支持通过多种操作与网页交互。
* 该工具提供控制浏览器会话、导航网页和提取信息的命令
* 它会在调用之间保持状态，直到显式关闭浏览器会话
* 当需要浏览网站、填写表单、点击按钮、提取内容或执行网页搜索时使用此工具
* 每个操作都需要工具依赖项中定义的特定参数

主要功能包括：
* 导航：访问特定URL、返回、搜索网页或刷新页面
* 交互：点击元素、输入文本、从下拉菜单中选择、发送键盘命令
* 滚动：按像素数量上下滚动或滚动到特定文本
* 内容提取：根据特定目标从网页中提取和分析内容
* 标签管理：切换标签页、打开新标签页或关闭标签页

注意：使用元素索引时，请参考当前浏览器状态中显示的元素编号。
"""

# 定义泛型类型变量
Context = TypeVar("Context")


# 浏览器工具类，继承自BaseTool并支持泛型
class BrowserUseTool(BaseTool, Generic[Context]):
    # 工具名称
    name: str = "browser_use"
    # 工具描述
    description: str = _BROWSER_DESCRIPTION
    # 工具参数定义
    parameters: dict = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "go_to_url",
                    "click_element",
                    "input_text",
                    "scroll_down",
                    "scroll_up",
                    "scroll_to_text",
                    "send_keys",
                    "get_dropdown_options",
                    "select_dropdown_option",
                    "go_back",
                    "web_search",
                    "wait",
                    "extract_content",
                    "switch_tab",
                    "open_tab",
                    "close_tab",
                ],
                "description": "要执行的浏览器操作",
            },
            "url": {
                "type": "string",
                "description": "用于'go_to_url'或'open_tab'操作的URL",
            },
            "index": {
                "type": "integer",
                "description": "用于'click_element'、'input_text'、'get_dropdown_options'或'select_dropdown_option'操作的元素索引",
            },
            "text": {
                "type": "string",
                "description": "用于'input_text'、'scroll_to_text'或'select_dropdown_option'操作的文本",
            },
            "scroll_amount": {
                "type": "integer",
                "description": "用于'scroll_down'或'scroll_up'操作的滚动像素数（正数为向下滚动，负数为向上滚动）",
            },
            "tab_id": {
                "type": "integer",
                "description": "用于'switch_tab'操作的标签页ID",
            },
            "query": {
                "type": "string",
                "description": "用于'web_search'操作的搜索查询",
            },
            "goal": {
                "type": "string",
                "description": "用于'extract_content'操作的提取目标",
            },
            "keys": {
                "type": "string",
                "description": "用于'send_keys'操作的按键",
            },
            "seconds": {
                "type": "integer",
                "description": "用于'wait'操作的等待秒数",
            },
        },
        "required": ["action"],
        "dependencies": {
            "go_to_url": ["url"],
            "click_element": ["index"],
            "input_text": ["index", "text"],
            "switch_tab": ["tab_id"],
            "open_tab": ["url"],
            "scroll_down": ["scroll_amount"],
            "scroll_up": ["scroll_amount"],
            "scroll_to_text": ["text"],
            "send_keys": ["keys"],
            "get_dropdown_options": ["index"],
            "select_dropdown_option": ["index", "text"],
            "go_back": [],
            "web_search": ["query"],
            "wait": ["seconds"],
            "extract_content": ["goal"],
        },
    }

    # 异步锁，确保线程安全
    lock: asyncio.Lock = Field(default_factory=asyncio.Lock)
    # 浏览器实例，初始为None
    browser: Optional[BrowserUseBrowser] = Field(default=None, exclude=True)
    # 浏览器上下文实例，初始为None
    context: Optional[BrowserContext] = Field(default=None, exclude=True)
    # DOM服务实例，初始为None
    dom_service: Optional[DomService] = Field(default=None, exclude=True)
    # 网页搜索工具实例
    web_search_tool: WebSearch = Field(default_factory=WebSearch, exclude=True)

    # 泛型上下文，用于扩展功能
    tool_context: Optional[Context] = Field(default=None, exclude=True)

    # LLM实例，用于内容提取
    llm: Optional[LLM] = Field(default_factory=LLM)

    # 参数验证器，确保参数不为空
    @field_validator("parameters", mode="before")
    def validate_parameters(cls, v: dict, info: ValidationInfo) -> dict:
        if not v:
            raise ValueError("参数不能为空")
        return v

    # 确保浏览器和上下文已初始化
    async def _ensure_browser_initialized(self) -> BrowserContext:
        """
        初始化浏览器和上下文，如果未初始化则创建实例。
        返回:
            BrowserContext: 初始化的浏览器上下文
        """
        if self.browser is None:
            # 浏览器配置参数
            browser_config_kwargs = {"headless": False, "disable_security": True}

            # 如果项目配置中有浏览器配置，则加载
            if config.browser_config:
                from browser_use.browser.browser import ProxySettings

                # 处理代理设置
                if config.browser_config.proxy and config.browser_config.proxy.server:
                    browser_config_kwargs["proxy"] = ProxySettings(
                        server=config.browser_config.proxy.server,
                        username=config.browser_config.proxy.username,
                        password=config.browser_config.proxy.password,
                    )

                # 加载其他浏览器配置属性
                browser_attrs = [
                    "headless",
                    "disable_security",
                    "extra_chromium_args",
                    "chrome_instance_path",
                    "wss_url",
                    "cdp_url",
                ]

                for attr in browser_attrs:
                    value = getattr(config.browser_config, attr, None)
                    if value is not None:
                        if not isinstance(value, list) or value:
                            browser_config_kwargs[attr] = value

            # 创建浏览器实例
            self.browser = BrowserUseBrowser(BrowserConfig(**browser_config_kwargs))

        # 如果上下文未初始化，则创建
        if self.context is None:
            context_config = BrowserContextConfig()

            # 如果项目配置中有上下文配置，则加载
            if (
                config.browser_config
                and hasattr(config.browser_config, "new_context_config")
                and config.browser_config.new_context_config
            ):
                context_config = config.browser_config.new_context_config

            # 创建上下文和DOM服务
            self.context = await self.browser.new_context(context_config)
            self.dom_service = DomService(await self.context.get_current_page())

        return self.context

    # 执行浏览器操作
    async def execute(
        self,
        action: str,
        url: Optional[str] = None,
        index: Optional[int] = None,
        text: Optional[str] = None,
        scroll_amount: Optional[int] = None,
        tab_id: Optional[int] = None,
        query: Optional[str] = None,
        goal: Optional[str] = None,
        keys: Optional[str] = None,
        seconds: Optional[int] = None,
        **kwargs,
    ) -> ToolResult:
        """
        执行指定的浏览器操作。

        参数:
            action: 要执行的浏览器操作
            url: 用于导航或新标签页的URL
            index: 用于点击或输入操作的元素索引
            text: 用于输入操作或搜索查询的文本
            scroll_amount: 用于滚动操作的滚动像素数
            tab_id: 用于切换标签页的标签页ID
            query: 用于网页搜索的查询
            goal: 用于内容提取的目标
            keys: 用于键盘操作的按键
            seconds: 用于等待操作的秒数
            **kwargs: 其他参数

        返回:
            ToolResult: 操作的结果或错误信息
        """
        async with self.lock:
            try:
                # 确保浏览器已初始化
                context = await self._ensure_browser_initialized()

                # 从配置中获取最大内容长度
                max_content_length = getattr(
                    config.browser_config, "max_content_length", 2000
                )

                # 导航操作
                if action == "go_to_url":
                    if not url:
                        return ToolResult(
                            error="'go_to_url'操作需要URL参数"
                        )
                    # 获取当前页面并导航到指定URL
                    page = await context.get_current_page()
                    await page.goto(url)
                    await page.wait_for_load_state()
                    return ToolResult(output=f"已导航到 {url}")

                elif action == "go_back":
                    # 返回上一页
                    await context.go_back()
                    return ToolResult(output="已返回上一页")

                elif action == "refresh":
                    # 刷新当前页面
                    await context.refresh_page()
                    return ToolResult(output="已刷新当前页面")

                elif action == "web_search":
                    if not query:
                        return ToolResult(
                            error="'web_search'操作需要查询参数"
                        )
                    # 执行网页搜索并直接返回结果，不进行浏览器导航
                    search_response = await self.web_search_tool.execute(
                        query=query, fetch_content=True, num_results=1
                    )
                    # 导航到第一个搜索结果
                    first_search_result = search_response.results[0]
                    url_to_navigate = first_search_result.url

                    page = await context.get_current_page()
                    await page.goto(url_to_navigate)
                    await page.wait_for_load_state()

                    return search_response

                # 元素交互操作
                elif action == "click_element":
                    if index is None:
                        return ToolResult(
                            error="'click_element'操作需要索引参数"
                        )
                    # 根据索引获取元素并点击
                    element = await context.get_dom_element_by_index(index)
                    if not element:
                        return ToolResult(error=f"未找到索引为 {index} 的元素")
                    download_path = await context._click_element_node(element)
                    output = f"已点击索引为 {index} 的元素"
                    if download_path:
                        output += f" - 文件已下载到 {download_path}"
                    return ToolResult(output=output)

                elif action == "input_text":
                    if index is None or not text:
                        return ToolResult(
                            error="'input_text'操作需要索引和文本参数"
                        )
                    # 根据索引获取元素并输入文本
                    element = await context.get_dom_element_by_index(index)
                    if not element:
                        return ToolResult(error=f"未找到索引为 {index} 的元素")
                    await context._input_text_element_node(element, text)
                    return ToolResult(
                        output=f"已在索引为 {index} 的元素中输入文本: '{text}'"
                    )

                elif action == "scroll_down" or action == "scroll_up":
                    # 确定滚动方向（1为向下，-1为向上）
                    direction = 1 if action == "scroll_down" else -1
                    # 获取滚动像素数，默认为浏览器窗口高度
                    amount = (
                        scroll_amount
                        if scroll_amount is not None
                        else context.config.browser_window_size["height"]
                    )
                    # 执行滚动操作
                    await context.execute_javascript(
                        f"window.scrollBy(0, {direction * amount});"
                    )
                    return ToolResult(
                        output=f"已{'向下' if direction > 0 else '向上'}滚动 {amount} 像素"
                    )

                elif action == "scroll_to_text":
                    if not text:
                        return ToolResult(
                            error="'scroll_to_text'操作需要文本参数"
                        )
                    # 滚动到包含指定文本的元素
                    page = await context.get_current_page()
                    try:
                        locator = page.get_by_text(text, exact=False)
                        await locator.scroll_into_view_if_needed()
                        return ToolResult(output=f"已滚动到文本: '{text}'")
                    except Exception as e:
                        return ToolResult(error=f"滚动到文本失败: {str(e)}")

                elif action == "send_keys":
                    if not keys:
                        return ToolResult(
                            error="'send_keys'操作需要按键参数"
                        )
                    # 发送键盘按键
                    page = await context.get_current_page()
                    await page.keyboard.press(keys)
                    return ToolResult(output=f"已发送按键: {keys}")

                elif action == "get_dropdown_options":
                    if index is None:
                        return ToolResult(
                            error="'get_dropdown_options'操作需要索引参数"
                        )
                    # 获取下拉菜单选项
                    element = await context.get_dom_element_by_index(index)
                    if not element:
                        return ToolResult(error=f"未找到索引为 {index} 的元素")
                    page = await context.get_current_page()
                    options = await page.evaluate(
                        """
                        (xpath) => {
                            const select = document.evaluate(xpath, document, null,
                                XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
                            if (!select) return null;
                            return Array.from(select.options).map(opt => ({
                                text: opt.text,
                                value: opt.value,
                                index: opt.index
                            }));
                        }
                    """,
                        element.xpath,
                    )
                    return ToolResult(output=f"下拉菜单选项: {options}")

                elif action == "select_dropdown_option":
                    if index is None or not text:
                        return ToolResult(
                            error="'select_dropdown_option'操作需要索引和文本参数"
                        )
                    # 选择下拉菜单选项
                    element = await context.get_dom_element_by_index(index)
                    if not element:
                        return ToolResult(error=f"未找到索引为 {index} 的元素")
                    page = await context.get_current_page()
                    await page.select_option(element.xpath, label=text)
                    return ToolResult(
                        output=f"已从索引为 {index} 的下拉菜单中选择选项: '{text}'"
                    )

                # 内容提取操作
                elif action == "extract_content":
                    if not goal:
                        return ToolResult(
                            error="'extract_content'操作需要目标参数"
                        )

                    # 获取当前页面内容并转换为Markdown格式
                    page = await context.get_current_page()
                    import markdownify

                    content = markdownify.markdownify(await page.content())

                    # 构建提取内容的提示
                    prompt = f"""\
您的任务是从页面中提取内容。您将获得一个页面和一个目标，您应该从页面中提取围绕此目标的所有相关信息。如果目标模糊，则总结页面。以JSON格式响应。
提取目标: {goal}

页面内容:
{content[:max_content_length]}
"""
                    messages = [{"role": "system", "content": prompt}]

                    # 定义提取功能的模式
                    extraction_function = {
                        "type": "function",
                        "function": {
                            "name": "extract_content",
                            "description": "根据目标从网页中提取特定信息",
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "extracted_content": {
                                        "type": "object",
                                        "description": "根据目标从页面中提取的内容",
                                        "properties": {
                                            "text": {
                                                "type": "string",
                                                "description": "从页面中提取的文本内容",
                                            },
                                            "metadata": {
                                                "type": "object",
                                                "description": "关于提取内容的附加元数据",
                                                "properties": {
                                                    "source": {
                                                        "type": "string",
                                                        "description": "提取内容的来源",
                                                    }
                                                },
                                            },
                                        },
                                    }
                                },
                                "required": ["extracted_content"],
                            },
                        },
                    }

                    # 使用LLM提取内容
                    response = await self.llm.ask_tool(
                        messages,
                        tools=[extraction_function],
                        tool_choice="required",
                    )

                    if response and response.tool_calls:
                        args = json.loads(response.tool_calls[0].function.arguments)
                        extracted_content = args.get("extracted_content", {})
                        return ToolResult(
                            output=f"从页面中提取的内容:\n{extracted_content}\n"
                        )

                    return ToolResult(output="未从页面中提取到内容。")

                # 标签管理操作
                elif action == "switch_tab":
                    if tab_id is None:
                        return ToolResult(
                            error="'switch_tab'操作需要标签页ID参数"
                        )
                    # 切换到指定标签页
                    await context.switch_to_tab(tab_id)
                    page = await context.get_current_page()
                    await page.wait_for_load_state()
                    return ToolResult(output=f"已切换到标签页 {tab_id}")

                elif action == "open_tab":
                    if not url:
                        return ToolResult(
                            error="'open_tab'操作需要URL参数"
                        )
                    # 打开新标签页
                    await context.create_new_tab(url)
                    return ToolResult(output=f"已打开新标签页: {url}")

                elif action == "close_tab":
                    # 关闭当前标签页
                    await context.close_current_tab()
                    return ToolResult(output="已关闭当前标签页")

                # 实用操作
                elif action == "wait":
                    seconds_to_wait = seconds if seconds is not None else 3
                    # 等待指定秒数
                    await asyncio.sleep(seconds_to_wait)
                    return ToolResult(output=f"已等待 {seconds_to_wait} 秒")

                else:
                    return ToolResult(error=f"未知操作: {action}")

            except Exception as e:
                return ToolResult(error=f"浏览器操作 '{action}' 失败: {str(e)}")

    # 获取当前浏览器状态
    async def get_current_state(
        self, context: Optional[BrowserContext] = None
    ) -> ToolResult:
        """
        获取当前浏览器状态。

        参数:
            context: 浏览器上下文，如果未提供则使用self.context

        返回:
            ToolResult: 包含浏览器状态或错误信息的结果
        """
        try:
            # 使用提供的上下文或默认上下文
            ctx = context or self.context
            if not ctx:
                return ToolResult(error="浏览器上下文未初始化")

            # 获取浏览器状态
            state = await ctx.get_state()

            # 获取视口高度
            viewport_height = 0
            if hasattr(state, "viewport_info") and state.viewport_info:
                viewport_height = state.viewport_info.height
            elif hasattr(ctx, "config") and hasattr(ctx.config, "browser_window_size"):
                viewport_height = ctx.config.browser_window_size.get("height", 0)

            # 截取当前页面截图
            page = await ctx.get_current_page()

            await page.bring_to_front()
            await page.wait_for_load_state()

            screenshot = await page.screenshot(
                full_page=True, animations="disabled", type="jpeg", quality=100
            )

            screenshot = base64.b64encode(screenshot).decode("utf-8")

            # 构建状态信息
            state_info = {
                "url": state.url,
                "title": state.title,
                "tabs": [tab.model_dump() for tab in state.tabs],
                "help": "[0], [1], [2], 等表示可点击的索引，对应列出的元素。点击这些索引将导航到或与它们背后的内容交互。",
                "interactive_elements": (
                    state.element_tree.clickable_elements_to_string()
                    if state.element_tree
                    else ""
                ),
                "scroll_info": {
                    "pixels_above": getattr(state, "pixels_above", 0),
                    "pixels_below": getattr(state, "pixels_below", 0),
                    "total_height": getattr(state, "pixels_above", 0)
                    + getattr(state, "pixels_below", 0)
                    + viewport_height,
                },
                "viewport_height": viewport_height,
            }

            return ToolResult(
                output=json.dumps(state_info, indent=4, ensure_ascii=False),
                base64_image=screenshot,
            )
        except Exception as e:
            return ToolResult(error=f"获取浏览器状态失败: {str(e)}")

    # 清理浏览器资源
    async def cleanup(self):
        """清理浏览器资源。"""
        async with self.lock:
            if self.context is not None:
                await self.context.close()
                self.context = None
                self.dom_service = None
            if self.browser is not None:
                await self.browser.close()
                self.browser = None

    # 对象销毁时清理资源
    def __del__(self):
        """确保对象销毁时清理资源。"""
        if self.browser is not None or self.context is not None:
            try:
                asyncio.run(self.cleanup())
            except RuntimeError:
                loop = asyncio.new_event_loop()
                loop.run_until_complete(self.cleanup())
                loop.close()

    # 工厂方法，创建带有特定上下文的BrowserUseTool实例
    @classmethod
    def create_with_context(cls, context: Context) -> "BrowserUseTool[Context]":
        """
        创建一个带有特定上下文的BrowserUseTool实例。

        参数:
            context: 上下文对象

        返回:
            BrowserUseTool: 新创建的实例
        """
        tool = cls()
        tool.tool_context = context
        return tool
