from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


# 基础工具类，抽象基类，用于定义工具的通用行为和属性
class BaseTool(ABC, BaseModel):
    # 工具名称
    name: str
    # 工具描述
    description: str
    # 工具参数，可选
    parameters: Optional[dict] = None

    class Config:
        # 允许任意类型作为字段
        arbitrary_types_allowed = True

    # 调用工具时执行的方法
    async def __call__(self, **kwargs) -> Any:
        """执行工具并返回结果"""
        return await self.execute(**kwargs)

    # 抽象方法，子类必须实现具体的执行逻辑
    @abstractmethod
    async def execute(self, **kwargs) -> Any:
        """执行工具的具体逻辑"""

    # 将工具转换为函数调用格式
    def to_param(self) -> Dict:
        """转换为函数调用格式的字典"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


# 工具执行结果类，用于封装工具的执行结果
class ToolResult(BaseModel):
    """工具执行结果的封装类"""

    # 工具的输出结果
    output: Any = Field(default=None)
    # 错误信息，可选
    error: Optional[str] = Field(default=None)
    # Base64编码的图片，可选
    base64_image: Optional[str] = Field(default=None)
    # 系统信息，可选
    system: Optional[str] = Field(default=None)

    class Config:
        # 允许任意类型作为字段
        arbitrary_types_allowed = True

    # 判断结果是否有效（任一字段非空则返回True）
    def __bool__(self):
        return any(getattr(self, field) for field in self.__fields__)

    # 合并两个工具结果
    def __add__(self, other: "ToolResult"):
        # 合并字段的逻辑
        def combine_fields(
            field: Optional[str], other_field: Optional[str], concatenate: bool = True
        ):
            if field and other_field:
                if concatenate:
                    return field + other_field
                raise ValueError("无法合并工具结果")
            return field or other_field

        return ToolResult(
            output=combine_fields(self.output, other.output),
            error=combine_fields(self.error, other.error),
            base64_image=combine_fields(self.base64_image, other.base64_image, False),
            system=combine_fields(self.system, other.system),
        )

    # 转换为字符串表示
    def __str__(self):
        return f"Error: {self.error}" if self.error else self.output

    # 替换字段值并返回新的ToolResult
    def replace(self, **kwargs):
        """替换字段值并返回新的ToolResult"""
        return type(self)(**{**self.dict(), **kwargs})


# CLI工具结果类，继承自ToolResult
class CLIResult(ToolResult):
    """用于CLI输出的工具结果类"""


# 工具执行失败类，继承自ToolResult
class ToolFailure(ToolResult):
    """表示工具执行失败的结果类"""
