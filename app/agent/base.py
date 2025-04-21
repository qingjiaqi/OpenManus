from abc import ABC, abstractmethod  # 导入抽象基类和抽象方法装饰器
from contextlib import asynccontextmanager  # 异步上下文管理器
from typing import List, Optional  # 类型注解支持

from pydantic import BaseModel, Field, model_validator  # 数据模型和字段验证

from app.llm import LLM  # 语言模型模块
from app.logger import logger  # 日志模块
from app.sandbox.client import SANDBOX_CLIENT  # 沙盒客户端
from app.schema import ROLE_TYPE, AgentState, Memory, Message  # 自定义类型和模型


class BaseAgent(BaseModel, ABC):
    """抽象基类，用于管理代理状态和执行流程。

    提供状态转换、内存管理和基于步骤的执行循环的基础功能。子类必须实现 `step` 方法。
    """

    # 核心属性
    name: str = Field(..., description="代理的唯一名称")
    description: Optional[str] = Field(None, description="代理的可选描述")

    # 提示词
    system_prompt: Optional[str] = Field(
        None, description="系统级指令提示词"
    )
    next_step_prompt: Optional[str] = Field(
        None, description="用于确定下一步操作的提示词"
    )

    # 依赖项
    llm: LLM = Field(default_factory=LLM, description="语言模型实例")
    memory: Memory = Field(default_factory=Memory, description="代理的内存存储")
    state: AgentState = Field(
        default=AgentState.IDLE, description="当前代理状态"
    )

    # 执行控制
    max_steps: int = Field(default=10, description="终止前的最大步骤数")
    current_step: int = Field(default=0, description="当前执行步骤")

    duplicate_threshold: int = 2  # 检测重复内容出现的次数阈值

    class Config:
        arbitrary_types_allowed = True  # 允许任意类型
        extra = "allow"  # 允许子类添加额外字段

    @model_validator(mode="after")
    def initialize_agent(self) -> "BaseAgent":
        """初始化代理，如果未提供设置则使用默认值。"""
        if self.llm is None or not isinstance(self.llm, LLM):
            self.llm = LLM(config_name=self.name.lower())  # 初始化语言模型
        if not isinstance(self.memory, Memory):
            self.memory = Memory()  # 初始化内存
        return self

    @asynccontextmanager
    async def state_context(self, new_state: AgentState):
        """安全的代理状态转换上下文管理器。

        参数:
            new_state: 上下文期间要转换到的状态。

        返回:
            None: 允许在新状态下执行代码。

        异常:
            ValueError: 如果 new_state 无效。
        """
        if not isinstance(new_state, AgentState):
            raise ValueError(f"无效状态: {new_state}")

        previous_state = self.state  # 保存当前状态
        self.state = new_state  # 更新状态
        try:
            yield
        except Exception as e:
            self.state = AgentState.ERROR  # 失败时转换为 ERROR 状态
            raise e
        finally:
            self.state = previous_state  # 恢复为之前的状态

    def update_memory(
        self,
        role: ROLE_TYPE,  # type: ignore
        content: str,
        base64_image: Optional[str] = None,
        **kwargs,
    ) -> None:
        """向代理的内存中添加一条消息。

        参数:
            role: 消息发送者的角色（user, system, assistant, tool）。
            content: 消息内容。
            base64_image: 可选的 base64 编码图像。
            **kwargs: 额外参数（例如工具消息的 tool_call_id）。

        异常:
            ValueError: 如果角色不受支持。
        """
        message_map = {
            "user": Message.user_message,
            "system": Message.system_message,
            "assistant": Message.assistant_message,
            "tool": lambda content, **kw: Message.tool_message(content, **kw),
        }

        if role not in message_map:
            raise ValueError(f"不支持的消息角色: {role}")

        # 根据角色创建消息
        kwargs = {"base64_image": base64_image, **(kwargs if role == "tool" else {})}
        self.memory.add_message(message_map[role](content, **kwargs))

    async def run(self, request: Optional[str] = None) -> str:
        """异步执行代理的主循环。

        参数:
            request: 可选的初始用户请求。

        返回:
            执行结果的字符串摘要。

        异常:
            RuntimeError: 如果代理启动时不在 IDLE 状态。
        """
        if self.state != AgentState.IDLE:
            raise RuntimeError(f"无法从状态启动代理: {self.state}")

        if request:
            self.update_memory("user", request)  # 更新内存

        results: List[str] = []
        async with self.state_context(AgentState.RUNNING):
            while (
                self.current_step < self.max_steps and self.state != AgentState.FINISHED
            ):
                self.current_step += 1
                logger.info(f"执行步骤 {self.current_step}/{self.max_steps}")
                step_result = await self.step()  # 执行单步操作

                # 检查是否在同一个步骤卡住
                if self.is_stuck():
                    self.handle_stuck_state()

                results.append(f"步骤 {self.current_step}: {step_result}")

            if self.current_step >= self.max_steps:
                self.current_step = 0
                self.state = AgentState.IDLE
                results.append(f"终止: 达到最大步骤数 ({self.max_steps})")
        await SANDBOX_CLIENT.cleanup()  # 清理沙盒
        return "\n".join(results) if results else "未执行任何步骤"

    @abstractmethod
    async def step(self) -> str:
        """执行代理工作流中的单步操作。

        必须由子类实现以定义具体行为。
        """

    def handle_stuck_state(self):
        """处理卡住状态，通过添加提示词改变策略。"""
        stuck_prompt = "\
        检测到重复响应。请考虑新策略，避免重复已尝试的无效路径。"
        self.next_step_prompt = f"{stuck_prompt}\n{self.next_step_prompt}"
        logger.warning(f"代理检测到卡住状态。添加提示词: {stuck_prompt}")

    def is_stuck(self) -> bool:
        """通过检测重复内容判断代理是否卡在循环中。"""
        if len(self.memory.messages) < 2:
            return False

        last_message = self.memory.messages[-1]
        if not last_message.content:
            return False

        # 统计相同内容的出现次数，如果是assistant返回并且内容和最后一次相同就加1
        duplicate_count = sum(
            1
            for msg in reversed(self.memory.messages[:-1])
            if msg.role == "assistant" and msg.content == last_message.content
        )

        return duplicate_count >= self.duplicate_threshold

    @property
    def messages(self) -> List[Message]:
        """从代理的内存中获取消息列表。"""
        return self.memory.messages

    @messages.setter
    def messages(self, value: List[Message]):
        """设置代理内存中的消息列表。"""
        self.memory.messages = value
