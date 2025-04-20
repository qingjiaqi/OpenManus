# 导入必要的模块
import math
from typing import Dict, List, Optional, Union

import tiktoken
from openai import (
    APIError,
    AsyncAzureOpenAI,
    AsyncOpenAI,
    AuthenticationError,
    OpenAIError,
    RateLimitError,
)
from openai.types.chat import ChatCompletion, ChatCompletionMessage
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_random_exponential,
)

from app.bedrock import BedrockClient
from app.config import LLMSettings, config
from app.exceptions import TokenLimitExceeded
from app.logger import logger  # 假设项目中已配置日志记录器
from app.schema import (
    ROLE_VALUES,
    TOOL_CHOICE_TYPE,
    TOOL_CHOICE_VALUES,
    Message,
    ToolChoice,
)


# 支持推理的模型列表
REASONING_MODELS = ["o1", "o3-mini"]
# 支持多模态（图像和文本）的模型列表
MULTIMODAL_MODELS = [
    "gpt-4-vision-preview",
    "gpt-4o",
    "gpt-4o-mini",
    "claude-3-opus-20240229",
    "claude-3-sonnet-20240229",
    "claude-3-haiku-20240307",
]


# 令牌计数器类，用于计算文本和图像的令牌数量
class TokenCounter:
    # 令牌常量
    BASE_MESSAGE_TOKENS = 4  # 每条消息的基础令牌数
    FORMAT_TOKENS = 2  # 格式令牌数
    LOW_DETAIL_IMAGE_TOKENS = 85  # 低细节图像的固定令牌数
    HIGH_DETAIL_TILE_TOKENS = 170  # 高细节图像每个瓦片的令牌数

    # 图像处理常量
    MAX_SIZE = 2048  # 图像最大尺寸
    HIGH_DETAIL_TARGET_SHORT_SIDE = 768  # 高细节目标短边尺寸
    TILE_SIZE = 512  # 瓦片尺寸

    def __init__(self, tokenizer):
        # 初始化令牌计数器，传入分词器
        self.tokenizer = tokenizer

    def count_text(self, text: str) -> int:
        """计算文本字符串的令牌数量"""
        return 0 if not text else len(self.tokenizer.encode(text))

    def count_image(self, image_item: dict) -> int:
        """
        根据细节级别和尺寸计算图像的令牌数量

        对于"low"细节：固定85令牌
        对于"high"细节：
        1. 缩放以适应2048x2048的正方形
        2. 缩放最短边至768px
        3. 计算512px的瓦片数量（每个瓦片170令牌）
        4. 添加85令牌
        """
        detail = image_item.get("detail", "medium")

        # 对于低细节，直接返回固定令牌数
        if detail == "low":
            return self.LOW_DETAIL_IMAGE_TOKENS

        # 对于中等细节（OpenAI默认），使用高细节计算
        # OpenAI没有为中等细节指定单独的计算方式

        # 对于高细节，如果提供了尺寸信息，则基于尺寸计算
        if detail == "high" or detail == "medium":
            # 如果图像项中包含尺寸信息
            if "dimensions" in image_item:
                width, height = image_item["dimensions"]
                return self._calculate_high_detail_tokens(width, height)

        # 默认值，当尺寸不可用或细节级别未知时
        if detail == "high":
            # 默认使用1024x1024图像的高细节计算
            return self._calculate_high_detail_tokens(1024, 1024)  # 765令牌
        elif detail == "medium":
            # 默认使用中等尺寸图像的令牌数
            return 1024  # 与原始默认值匹配
        else:
            # 对于未知细节级别，默认使用中等细节
            return 1024

    def _calculate_high_detail_tokens(self, width: int, height: int) -> int:
        """根据尺寸计算高细节图像的令牌数量"""
        # 步骤1：缩放以适应MAX_SIZE x MAX_SIZE的正方形
        if width > self.MAX_SIZE or height > self.MAX_SIZE:
            scale = self.MAX_SIZE / max(width, height)
            width = int(width * scale)
            height = int(height * scale)

        # 步骤2：缩放最短边至HIGH_DETAIL_TARGET_SHORT_SIDE
        scale = self.HIGH_DETAIL_TARGET_SHORT_SIDE / min(width, height)
        scaled_width = int(width * scale)
        scaled_height = int(height * scale)

        # 步骤3：计算512px的瓦片数量
        tiles_x = math.ceil(scaled_width / self.TILE_SIZE)
        tiles_y = math.ceil(scaled_height / self.TILE_SIZE)
        total_tiles = tiles_x * tiles_y

        # 步骤4：计算最终令牌数量
        return (
            total_tiles * self.HIGH_DETAIL_TILE_TOKENS
        ) + self.LOW_DETAIL_IMAGE_TOKENS

    def count_content(self, content: Union[str, List[Union[str, dict]]]) -> int:
        """计算消息内容的令牌数量"""
        if not content:
            return 0

        if isinstance(content, str):
            return self.count_text(content)

        token_count = 0
        for item in content:
            if isinstance(item, str):
                token_count += self.count_text(item)
            elif isinstance(item, dict):
                if "text" in item:
                    token_count += self.count_text(item["text"])
                elif "image_url" in item:
                    token_count += self.count_image(item)
        return token_count

    def count_tool_calls(self, tool_calls: List[dict]) -> int:
        """计算工具调用的令牌数量"""
        token_count = 0
        for tool_call in tool_calls:
            if "function" in tool_call:
                function = tool_call["function"]
                token_count += self.count_text(function.get("name", ""))
                token_count += self.count_text(function.get("arguments", ""))
        return token_count

    def count_message_tokens(self, messages: List[dict]) -> int:
        """计算消息列表中所有消息的总令牌数量"""
        total_tokens = self.FORMAT_TOKENS  # 基础格式令牌

        for message in messages:
            tokens = self.BASE_MESSAGE_TOKENS  # 每条消息的基础令牌

            # 添加角色令牌
            tokens += self.count_text(message.get("role", ""))

            # 添加内容令牌
            if "content" in message:
                tokens += self.count_content(message["content"])

            # 添加工具调用令牌
            if "tool_calls" in message:
                tokens += self.count_tool_calls(message["tool_calls"])

            # 添加名称和tool_call_id令牌
            tokens += self.count_text(message.get("name", ""))
            tokens += self.count_text(message.get("tool_call_id", ""))

            total_tokens += tokens

        return total_tokens


# LLM 类，用于管理语言模型交互
class LLM:
    _instances: Dict[str, "LLM"] = {}  # 存储单例实例的字典

    def __new__(
        cls, config_name: str = "default", llm_config: Optional[LLMSettings] = None
    ):
        # 实现单例模式，确保每个配置名称只有一个实例
        if config_name not in cls._instances:
            instance = super().__new__(cls)
            instance.__init__(config_name, llm_config)
            cls._instances[config_name] = instance
        return cls._instances[config_name]

    def __init__(
        self, config_name: str = "default", llm_config: Optional[LLMSettings] = None
    ):
        # 初始化 LLM 实例，仅当未初始化时执行
        if not hasattr(self, "client"):
            llm_config = llm_config or config.llm
            llm_config = llm_config.get(config_name, llm_config["default"])
            self.model = llm_config.model  # 模型名称
            self.max_tokens = llm_config.max_tokens  # 最大令牌数
            self.temperature = llm_config.temperature  # 温度参数
            self.api_type = llm_config.api_type  # API 类型（如 "azure"、"aws"）
            self.api_key = llm_config.api_key  # API 密钥
            self.api_version = llm_config.api_version  # API 版本（仅 Azure 需要）
            self.base_url = llm_config.base_url  # API 基础 URL

            # 令牌计数相关属性
            self.total_input_tokens = 0  # 累计输入令牌数
            self.total_completion_tokens = 0  # 累计完成令牌数
            self.max_input_tokens = (
                llm_config.max_input_tokens
                if hasattr(llm_config, "max_input_tokens")
                else None
            )  # 最大输入令牌限制

            # 初始化分词器
            try:
                self.tokenizer = tiktoken.encoding_for_model(self.model)
            except KeyError:
                # 如果模型不在 tiktoken 预设中，默认使用 cl100k_base
                self.tokenizer = tiktoken.get_encoding("cl100k_base")

            # 根据 API 类型初始化客户端
            if self.api_type == "azure":
                self.client = AsyncAzureOpenAI(
                    base_url=self.base_url,
                    api_key=self.api_key,
                    api_version=self.api_version,
                )
            elif self.api_type == "aws":
                self.client = BedrockClient()
            else:
                self.client = AsyncOpenAI(api_key=self.api_key, base_url=self.base_url)

            # 初始化令牌计数器
            self.token_counter = TokenCounter(self.tokenizer)

    def count_tokens(self, text: str) -> int:
        """计算文本的令牌数量"""
        if not text:
            return 0
        return len(self.tokenizer.encode(text))

    def count_message_tokens(self, messages: List[dict]) -> int:
        """计算消息列表的令牌数量"""
        return self.token_counter.count_message_tokens(messages)

    def update_token_count(self, input_tokens: int, completion_tokens: int = 0) -> None:
        """更新令牌计数"""
        # 仅当设置了 max_input_tokens 时才跟踪令牌
        self.total_input_tokens += input_tokens
        self.total_completion_tokens += completion_tokens
        logger.info(
            f"Token usage: Input={input_tokens}, Completion={completion_tokens}, "
            f"Cumulative Input={self.total_input_tokens}, Cumulative Completion={self.total_completion_tokens}, "
            f"Total={input_tokens + completion_tokens}, Cumulative Total={self.total_input_tokens + self.total_completion_tokens}"
        )

    def check_token_limit(self, input_tokens: int) -> bool:
        """检查令牌限制是否超出"""
        if self.max_input_tokens is not None:
            return (self.total_input_tokens + input_tokens) <= self.max_input_tokens
        # 如果未设置 max_input_tokens，始终返回 True
        return True

    def get_limit_error_message(self, input_tokens: int) -> str:
        """生成令牌限制超出时的错误消息"""
        if (
            self.max_input_tokens is not None
            and (self.total_input_tokens + input_tokens) > self.max_input_tokens
        ):
            return f"Request may exceed input token limit (Current: {self.total_input_tokens}, Needed: {input_tokens}, Max: {self.max_input_tokens})"

        return "Token limit exceeded"

    @staticmethod
    def format_messages(
        messages: List[Union[dict, Message]], supports_images: bool = False
    ) -> List[dict]:
        """
        将消息列表格式化为 OpenAI 消息格式。

        参数:
            messages: 消息列表，可以是字典或 Message 对象
            supports_images: 目标模型是否支持图像输入

        返回:
            List[dict]: 格式化后的消息列表

        异常:
            ValueError: 如果消息无效或缺少必填字段
            TypeError: 如果提供了不支持的消息类型

        示例:
            >>> msgs = [
            ...     Message.system_message("You are a helpful assistant"),
            ...     {"role": "user", "content": "Hello"},
            ...     Message.user_message("How are you?")
            ... ]
            >>> formatted = LLM.format_messages(msgs)
        """
        formatted_messages = []

        for message in messages:
            # 将 Message 对象转换为字典
            if isinstance(message, Message):
                message = message.to_dict()

            if isinstance(message, dict):
                # 如果消息是字典，确保包含必填字段
                if "role" not in message:
                    raise ValueError("Message dict must contain 'role' field")

                # 如果模型支持图像且消息包含 base64_image，则处理图像
                if supports_images and message.get("base64_image"):
                    # 初始化或转换内容为适当格式
                    if not message.get("content"):
                        message["content"] = []
                    elif isinstance(message["content"], str):
                        message["content"] = [
                            {"type": "text", "text": message["content"]}
                        ]
                    elif isinstance(message["content"], list):
                        # 将字符串项转换为文本对象
                        message["content"] = [
                            (
                                {"type": "text", "text": item}
                                if isinstance(item, str)
                                else item
                            )
                            for item in message["content"]
                        ]

                    # 将图像添加到内容中
                    message["content"].append(
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{message['base64_image']}"
                            },
                        }
                    )

                    # 删除 base64_image 字段
                    del message["base64_image"]
                # 如果模型不支持图像但消息包含 base64_image，则忽略图像
                elif not supports_images and message.get("base64_image"):
                    # 删除 base64_image 字段并保留文本内容
                    del message["base64_image"]

                if "content" in message or "tool_calls" in message:
                    formatted_messages.append(message)
                # 否则不包含该消息
            else:
                raise TypeError(f"Unsupported message type: {type(message)}")

        # 验证所有消息的必填字段
        for msg in formatted_messages:
            if msg["role"] not in ROLE_VALUES:
                raise ValueError(f"Invalid role: {msg['role']}")

        return formatted_messages

    @retry(
        wait=wait_random_exponential(min=1, max=60),
        stop=stop_after_attempt(6),
        retry=retry_if_exception_type(
            (OpenAIError, Exception, ValueError)
        ),  # 不重试 TokenLimitExceeded
    )
    async def ask(
        self,
        messages: List[Union[dict, Message]],
        system_msgs: Optional[List[Union[dict, Message]]] = None,
        stream: bool = True,
        temperature: Optional[float] = None,
    ) -> str:
        """
        向 LLM 发送提示并获取响应。

        参数:
            messages: 对话消息列表
            system_msgs: 可选的系统消息列表
            stream: 是否流式传输响应
            temperature: 响应采样温度

        返回:
            str: 生成的响应

        异常:
            TokenLimitExceeded: 如果令牌限制超出
            ValueError: 如果消息无效或响应为空
            OpenAIError: 如果 API 调用失败
            Exception: 其他意外错误
        """
        try:
            # 检查模型是否支持图像
            supports_images = self.model in MULTIMODAL_MODELS

            # 格式化系统消息和用户消息
            if system_msgs:
                system_msgs = self.format_messages(system_msgs, supports_images)
                messages = system_msgs + self.format_messages(messages, supports_images)
            else:
                messages = self.format_messages(messages, supports_images)

            # 计算输入令牌数量
            input_tokens = self.count_message_tokens(messages)

            # 检查令牌限制是否超出
            if not self.check_token_limit(input_tokens):
                error_message = self.get_limit_error_message(input_tokens)
                # 抛出不会被重试的特殊异常
                raise TokenLimitExceeded(error_message)

            # 设置 API 参数
            params = {
                "model": self.model,
                "messages": messages,
            }

            # 添加模型特定参数
            if self.model in REASONING_MODELS:
                params["max_completion_tokens"] = self.max_tokens
            else:
                params["max_tokens"] = self.max_tokens
                params["temperature"] = (
                    temperature if temperature is not None else self.temperature
                )

            if not stream:
                # 非流式请求
                response = await self.client.chat.completions.create(
                    **params, stream=False
                )

                if not response.choices or not response.choices[0].message.content:
                    raise ValueError("Empty or invalid response from LLM")

                # 更新令牌计数
                self.update_token_count(
                    response.usage.prompt_tokens, response.usage.completion_tokens
                )

                return response.choices[0].message.content

            # 流式请求，在请求前更新令牌计数
            self.update_token_count(input_tokens)

            response = await self.client.chat.completions.create(**params, stream=True)

            collected_messages = []
            completion_text = ""
            async for chunk in response:
                chunk_message = chunk.choices[0].delta.content or ""
                collected_messages.append(chunk_message)
                completion_text += chunk_message
                print(chunk_message, end="", flush=True)

            print()  # 流式传输后换行
            full_response = "".join(collected_messages).strip()
            if not full_response:
                raise ValueError("Empty response from streaming LLM")

            # 估计流式响应的完成令牌数量
            completion_tokens = self.count_tokens(completion_text)
            logger.info(
                f"Estimated completion tokens for streaming response: {completion_tokens}"
            )
            self.total_completion_tokens += completion_tokens

            return full_response

        except TokenLimitExceeded:
            # 重新抛出令牌限制异常
            raise
        except ValueError:
            logger.exception(f"Validation error")
            raise
        except OpenAIError as oe:
            logger.exception(f"OpenAI API error")
            if isinstance(oe, AuthenticationError):
                logger.error("Authentication failed. Check API key.")
            elif isinstance(oe, RateLimitError):
                logger.error("Rate limit exceeded. Consider increasing retry attempts.")
            elif isinstance(oe, APIError):
                logger.error(f"API error: {oe}")
            raise
        except Exception:
            logger.exception(f"Unexpected error in ask")
            raise

    @retry(
        wait=wait_random_exponential(min=1, max=60),
        stop=stop_after_attempt(6),
        retry=retry_if_exception_type(
            (OpenAIError, Exception, ValueError)
        ),  # 不重试 TokenLimitExceeded
    )
    async def ask_with_images(
        self,
        messages: List[Union[dict, Message]],
        images: List[Union[str, dict]],
        system_msgs: Optional[List[Union[dict, Message]]] = None,
        stream: bool = False,
        temperature: Optional[float] = None,
    ) -> str:
        """
        向 LLM 发送包含图像的提示并获取响应。

        参数:
            messages: 对话消息列表
            images: 图像 URL 或图像数据字典列表
            system_msgs: 可选的系统消息列表
            stream: 是否流式传输响应
            temperature: 响应采样温度

        返回:
            str: 生成的响应

        异常:
            TokenLimitExceeded: 如果令牌限制超出
            ValueError: 如果消息无效或响应为空
            OpenAIError: 如果 API 调用失败
            Exception: 其他意外错误
        """
        try:
            # 检查模型是否支持图像
            if self.model not in MULTIMODAL_MODELS:
                raise ValueError(
                    f"Model {self.model} does not support images. Use a model from {MULTIMODAL_MODELS}"
                )

            # 格式化消息并支持图像
            formatted_messages = self.format_messages(messages, supports_images=True)

            # 确保最后一条消息来自用户，以便附加图像
            if not formatted_messages or formatted_messages[-1]["role"] != "user":
                raise ValueError(
                    "The last message must be from the user to attach images"
                )

            # 处理最后一条用户消息以包含图像
            last_message = formatted_messages[-1]

            # 将内容转换为多模态格式
            content = last_message["content"]
            multimodal_content = (
                [{"type": "text", "text": content}]
                if isinstance(content, str)
                else content
                if isinstance(content, list)
                else []
            )

            # 将图像添加到内容中
            for image in images:
                if isinstance(image, str):
                    multimodal_content.append(
                        {"type": "image_url", "image_url": {"url": image}}
                    )
                elif isinstance(image, dict) and "url" in image:
                    multimodal_content.append({"type": "image_url", "image_url": image})
                elif isinstance(image, dict) and "image_url" in image:
                    multimodal_content.append(image)
                else:
                    raise ValueError(f"Unsupported image format: {image}")

            # 更新消息内容为多模态格式
            last_message["content"] = multimodal_content

            # 添加系统消息（如果提供）
            if system_msgs:
                all_messages = (
                    self.format_messages(system_msgs, supports_images=True)
                    + formatted_messages
                )
            else:
                all_messages = formatted_messages

            # 计算令牌并检查限制
            input_tokens = self.count_message_tokens(all_messages)
            if not self.check_token_limit(input_tokens):
                raise TokenLimitExceeded(self.get_limit_error_message(input_tokens))

            # 设置 API 参数
            params = {
                "model": self.model,
                "messages": all_messages,
                "stream": stream,
            }

            # 添加模型特定参数
            if self.model in REASONING_MODELS:
                params["max_completion_tokens"] = self.max_tokens
            else:
                params["max_tokens"] = self.max_tokens
                params["temperature"] = (
                    temperature if temperature is not None else self.temperature
                )

            # 处理非流式请求
            if not stream:
                response = await self.client.chat.completions.create(**params)

                if not response.choices or not response.choices[0].message.content:
                    raise ValueError("Empty or invalid response from LLM")

                self.update_token_count(response.usage.prompt_tokens)
                return response.choices[0].message.content

            # 处理流式请求
            self.update_token_count(input_tokens)
            response = await self.client.chat.completions.create(**params)

            collected_messages = []
            async for chunk in response:
                chunk_message = chunk.choices[0].delta.content or ""
                collected_messages.append(chunk_message)
                print(chunk_message, end="", flush=True)

            print()  # 流式传输后换行
            full_response = "".join(collected_messages).strip()

            if not full_response:
                raise ValueError("Empty response from streaming LLM")

            return full_response

        except TokenLimitExceeded:
            raise
        except ValueError as ve:
            logger.error(f"Validation error in ask_with_images: {ve}")
            raise
        except OpenAIError as oe:
            logger.error(f"OpenAI API error: {oe}")
            if isinstance(oe, AuthenticationError):
                logger.error("Authentication failed. Check API key.")
            elif isinstance(oe, RateLimitError):
                logger.error("Rate limit exceeded. Consider increasing retry attempts.")
            elif isinstance(oe, APIError):
                logger.error(f"API error: {oe}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error in ask_with_images: {e}")
            raise

    @retry(
        wait=wait_random_exponential(min=1, max=60),
        stop=stop_after_attempt(6),
        retry=retry_if_exception_type(
            (OpenAIError, Exception, ValueError)
        ),  # 不重试 TokenLimitExceeded
    )
    async def ask_tool(
        self,
        messages: List[Union[dict, Message]],
        system_msgs: Optional[List[Union[dict, Message]]] = None,
        timeout: int = 300,
        tools: Optional[List[dict]] = None,
        tool_choice: TOOL_CHOICE_TYPE = ToolChoice.AUTO,  # type: ignore
        temperature: Optional[float] = None,
        **kwargs,
    ) -> ChatCompletionMessage | None:
        """
        使用工具/函数向 LLM 发送提示并获取响应。

        参数:
            messages: 对话消息列表
            system_msgs: 可选的系统消息列表
            timeout: 请求超时时间（秒）
            tools: 使用的工具列表
            tool_choice: 工具选择策略
            temperature: 响应采样温度
            **kwargs: 其他完成参数

        返回:
            ChatCompletionMessage: 模型的响应

        异常:
            TokenLimitExceeded: 如果令牌限制超出
            ValueError: 如果工具、tool_choice 或消息无效
            OpenAIError: 如果 API 调用失败
            Exception: 其他意外错误
        """
        try:
            # 验证 tool_choice
            if tool_choice not in TOOL_CHOICE_VALUES:
                raise ValueError(f"Invalid tool_choice: {tool_choice}")

            # 检查模型是否支持图像
            supports_images = self.model in MULTIMODAL_MODELS

            # 格式化消息
            if system_msgs:
                system_msgs = self.format_messages(system_msgs, supports_images)
                messages = system_msgs + self.format_messages(messages, supports_images)
            else:
                messages = self.format_messages(messages, supports_images)

            # 计算输入令牌数量
            input_tokens = self.count_message_tokens(messages)

            # 如果提供了工具，计算工具描述的令牌数量
            tools_tokens = 0
            if tools:
                for tool in tools:
                    tools_tokens += self.count_tokens(str(tool))

            input_tokens += tools_tokens

            # 检查令牌限制是否超出
            if not self.check_token_limit(input_tokens):
                error_message = self.get_limit_error_message(input_tokens)
                # 抛出不会被重试的特殊异常
                raise TokenLimitExceeded(error_message)

            # 验证工具（如果提供）
            if tools:
                for tool in tools:
                    if not isinstance(tool, dict) or "type" not in tool:
                        raise ValueError("Each tool must be a dict with 'type' field")

            # 设置完成请求参数
            params = {
                "model": self.model,
                "messages": messages,
                "tools": tools,
                "tool_choice": tool_choice,
                "timeout": timeout,
                **kwargs,
            }

            if self.model in REASONING_MODELS:
                params["max_completion_tokens"] = self.max_tokens
            else:
                params["max_tokens"] = self.max_tokens
                params["temperature"] = (
                    temperature if temperature is not None else self.temperature
                )

            params["stream"] = False  # 工具请求始终使用非流式
            response: ChatCompletion = await self.client.chat.completions.create(
                **params
            )

            # 检查响应是否有效
            if not response.choices or not response.choices[0].message:
                print(response)
                return None

            # 更新令牌计数
            self.update_token_count(
                response.usage.prompt_tokens, response.usage.completion_tokens
            )

            return response.choices[0].message

        except TokenLimitExceeded:
            # 重新抛出令牌限制异常
            raise
        except ValueError as ve:
            logger.error(f"Validation error in ask_tool: {ve}")
            raise
        except OpenAIError as oe:
            logger.error(f"OpenAI API error: {oe}")
            if isinstance(oe, AuthenticationError):
                logger.error("Authentication failed. Check API key.")
            elif isinstance(oe, RateLimitError):
                logger.error("Rate limit exceeded. Consider increasing retry attempts.")
            elif isinstance(oe, APIError):
                logger.error(f"API error: {oe}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error in ask_tool: {e}")
            raise
