from abc import ABC, abstractmethod  # 导入抽象基类模块，用于定义抽象类和抽象方法
from typing import Dict, List, Optional, Union  # 导入类型注解模块，用于类型提示

from pydantic import BaseModel  # 导入Pydantic的BaseModel，用于数据验证和设置

from app.agent.base import BaseAgent  # 导入自定义的BaseAgent类


class BaseFlow(BaseModel, ABC):
    """
    基础流程类，支持多代理执行的抽象基类
    - 继承自Pydantic的BaseModel，提供数据验证功能
    - 继承自ABC（抽象基类），定义抽象方法
    """

    agents: Dict[str, BaseAgent]  # 存储代理的字典，键为代理名称，值为代理实例
    tools: Optional[List] = None  # 可选的工具列表，默认为None
    primary_agent_key: Optional[str] = None  # 主代理的键名，默认为None

    class Config:
        arbitrary_types_allowed = True  # 允许任意类型，避免Pydantic的类型检查报错

    def __init__(
        self, agents: Union[BaseAgent, List[BaseAgent], Dict[str, BaseAgent]], **data
    ):
        """
        初始化方法，支持多种代理输入方式
        - agents: 可以是单个代理、代理列表或代理字典
        - **data: 其他初始化参数
        """
        # 处理不同类型的代理输入
        if isinstance(agents, BaseAgent):  # 如果输入是单个代理
            agents_dict = {"default": agents}  # 创建一个默认键的字典
        elif isinstance(agents, list):  # 如果输入是代理列表
            agents_dict = {f"agent_{i}": agent for i, agent in enumerate(agents)}  # 为每个代理生成键
        else:  # 如果输入已经是字典
            agents_dict = agents  # 直接使用输入的字典

        # 如果未指定主代理键名，则使用第一个代理作为主代理
        primary_key = data.get("primary_agent_key")
        if not primary_key and agents_dict:
            primary_key = next(iter(agents_dict))  # 获取字典的第一个键
            data["primary_agent_key"] = primary_key  # 设置主代理键名

        # 设置代理字典
        data["agents"] = agents_dict

        # 调用父类的初始化方法
        super().__init__(**data)

    @property
    def primary_agent(self) -> Optional[BaseAgent]:
        """
        获取主代理
        - 返回值: 主代理实例，如果未设置则返回None
        """
        return self.agents.get(self.primary_agent_key)

    def get_agent(self, key: str) -> Optional[BaseAgent]:
        """
        根据键名获取指定代理
        - key: 代理的键名
        - 返回值: 代理实例，如果键名不存在则返回None
        """
        return self.agents.get(key)

    def add_agent(self, key: str, agent: BaseAgent) -> None:
        """
        添加新代理到流程中
        - key: 代理的键名
        - agent: 代理实例
        """
        self.agents[key] = agent

    @abstractmethod
    async def execute(self, input_text: str) -> str:
        """
        抽象方法，执行流程
        - input_text: 输入文本
        - 返回值: 执行结果文本
        """
        ...
