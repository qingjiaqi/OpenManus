from abc import ABC, abstractmethod  # 导入抽象基类模块，用于定义抽象类和抽象方法
from typing import Optional  # 导入Optional类型，用于可选参数的类型提示

from pydantic import Field  # 导入Pydantic的Field，用于定义模型字段

from app.agent.base import BaseAgent  # 导入基础Agent类
from app.llm import LLM  # 导入LLM（大语言模型）模块
from app.schema import AgentState, Memory  # 导入Agent状态和内存模型


class ReActAgent(BaseAgent, ABC):
    """ReAct（Reasoning and Acting）Agent的抽象基类。

    该类结合了推理（Reasoning）和行动（Acting）的能力，适用于需要动态决策的任务。
    """

    name: str  # Agent名称
    description: Optional[str] = None  # Agent描述，可选

    system_prompt: Optional[str] = None  # 系统提示模板，可选
    next_step_prompt: Optional[str] = None  # 下一步提示模板，可选

    llm: Optional[LLM] = Field(default_factory=LLM)  # 大语言模型实例，可选
    memory: Memory = Field(default_factory=Memory)  # 内存模块，用于存储交互历史
    state: AgentState = AgentState.IDLE  # Agent当前状态，默认为空闲

    max_steps: int = 10  # 最大执行步数
    current_step: int = 0  # 当前执行步数

    @abstractmethod
    async def think(self) -> bool:
        """处理当前状态并决定下一步操作。

        返回:
            布尔值，表示是否需要执行行动
        """

    @abstractmethod
    async def act(self) -> str:
        """执行决策的行动。

        返回:
            字符串，表示行动的结果
        """

    async def step(self) -> str:
        """执行单步操作：思考并行动。

        返回:
            字符串，表示行动的结果或无需行动的信息
        """
        should_act = await self.think()  # 调用think方法决定是否需要行动
        if not should_act:
            return "Thinking complete - no action needed"  # 无需行动时返回提示
        return await self.act()  # 需要行动时调用act方法