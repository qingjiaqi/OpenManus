from enum import Enum  # 导入枚举类，用于定义常量
from typing import Dict, List, Union  # 导入类型注解工具，用于定义变量类型

from app.agent.base import BaseAgent  # 导入基础Agent类
from app.flow.base import BaseFlow  # 导入基础Flow类
from app.flow.planning import PlanningFlow  # 导入PlanningFlow类


class FlowType(str, Enum):
    """定义Flow类型的枚举类，用于标识不同的Flow类型"""
    PLANNING = "planning"  # 规划类型的Flow


class FlowFactory:
    """Factory for creating different types of flows with support for multiple agents"""
    """Flow工厂类，用于创建不同类型的Flow，支持多Agent"""

    @staticmethod
    def create_flow(
        flow_type: FlowType,  # Flow类型，必须是FlowType枚举值
        agents: Union[BaseAgent, List[BaseAgent], Dict[str, BaseAgent]],  # 单个Agent、Agent列表或Agent字典
        **kwargs,  # 其他可选参数
    ) -> BaseFlow:
        """创建并返回指定类型的Flow实例"""
        flows = {
            FlowType.PLANNING: PlanningFlow,  # 映射Flow类型到对应的Flow类
        }

        flow_class = flows.get(flow_type)  # 根据flow_type获取对应的Flow类
        if not flow_class:
            raise ValueError(f"Unknown flow type: {flow_type}")  # 如果类型不支持，抛出异常

        return flow_class(agents, **kwargs)  # 创建并返回Flow实例