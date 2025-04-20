# 导入必要的类型和模块
from typing import Any, List, Optional, Type, Union, get_args, get_origin

from pydantic import BaseModel, Field

from app.tool import BaseTool


# 定义 CreateChatCompletion 类，继承自 BaseTool
class CreateChatCompletion(BaseTool):
    # 工具名称
    name: str = "create_chat_completion"
    # 工具描述：用于生成结构化响应
    description: str = (
        "Creates a structured completion with specified output formatting."
    )

    # 类型映射字典，用于将 Python 类型映射为 JSON Schema 类型
    type_mapping: dict = {
        str: "string",
        int: "integer",
        float: "number",
        bool: "boolean",
        dict: "object",
        list: "array",
    }
    # 响应类型，默认为 None
    response_type: Optional[Type] = None
    # 必填字段列表，默认为 ["response"]
    required: List[str] = Field(default_factory=lambda: ["response"])

    def __init__(self, response_type: Optional[Type] = str):
        """
        初始化工具，指定响应类型
        Args:
            response_type: 响应类型，默认为 str
        """
        super().__init__()
        self.response_type = response_type
        self.parameters = self._build_parameters()

    def _build_parameters(self) -> dict:
        """
        根据响应类型构建参数 Schema
        Returns:
            dict: 参数 Schema
        """
        if self.response_type == str:
            return {
                "type": "object",
                "properties": {
                    "response": {
                        "type": "string",
                        "description": "The response text that should be delivered to the user.",
                    },
                },
                "required": self.required,
            }

        if isinstance(self.response_type, type) and issubclass(
            self.response_type, BaseModel
        ):
            schema = self.response_type.model_json_schema()
            return {
                "type": "object",
                "properties": schema["properties"],
                "required": schema.get("required", self.required),
            }

        return self._create_type_schema(self.response_type)

    def _create_type_schema(self, type_hint: Type) -> dict:
        """
        为给定类型创建 JSON Schema
        Args:
            type_hint: 类型提示
        Returns:
            dict: JSON Schema
        """
        origin = get_origin(type_hint)
        args = get_args(type_hint)

        # 处理基本类型
        if origin is None:
            return {
                "type": "object",
                "properties": {
                    "response": {
                        "type": self.type_mapping.get(type_hint, "string"),
                        "description": f"Response of type {type_hint.__name__}",
                    }
                },
                "required": self.required,
            }

        # 处理 List 类型
        if origin is list:
            item_type = args[0] if args else Any
            return {
                "type": "object",
                "properties": {
                    "response": {
                        "type": "array",
                        "items": self._get_type_info(item_type),
                    }
                },
                "required": self.required,
            }

        # 处理 Dict 类型
        if origin is dict:
            value_type = args[1] if len(args) > 1 else Any
            return {
                "type": "object",
                "properties": {
                    "response": {
                        "type": "object",
                        "additionalProperties": self._get_type_info(value_type),
                    }
                },
                "required": self.required,
            }

        # 处理 Union 类型
        if origin is Union:
            return self._create_union_schema(args)

        return self._build_parameters()

    def _get_type_info(self, type_hint: Type) -> dict:
        """
        获取单个类型的类型信息
        Args:
            type_hint: 类型提示
        Returns:
            dict: 类型信息
        """
        if isinstance(type_hint, type) and issubclass(type_hint, BaseModel):
            return type_hint.model_json_schema()

        return {
            "type": self.type_mapping.get(type_hint, "string"),
            "description": f"Value of type {getattr(type_hint, '__name__', 'any')}",
        }

    def _create_union_schema(self, types: tuple) -> dict:
        """
        为 Union 类型创建 Schema
        Args:
            types: 类型元组
        Returns:
            dict: Schema
        """
        return {
            "type": "object",
            "properties": {
                "response": {"anyOf": [self._get_type_info(t) for t in types]}
            },
            "required": self.required,
        }

    async def execute(self, required: list | None = None, **kwargs) -> Any:
        """
        执行聊天完成逻辑，并进行类型转换
        Args:
            required: 必填字段列表，默认为 None
            **kwargs: 响应数据
        Returns:
            Any: 根据 response_type 转换后的响应
        """
        required = required or self.required

        # 处理必填字段为列表的情况
        if isinstance(required, list) and len(required) > 0:
            if len(required) == 1:
                required_field = required[0]
                result = kwargs.get(required_field, "")
            else:
                # 返回多个字段的字典
                return {field: kwargs.get(field, "") for field in required}
        else:
            required_field = "response"
            result = kwargs.get(required_field, "")

        # 类型转换逻辑
        if self.response_type == str:
            return result

        if isinstance(self.response_type, type) and issubclass(
            self.response_type, BaseModel
        ):
            return self.response_type(**kwargs)

        if get_origin(self.response_type) in (list, dict):
            return result  # 假设结果已经是正确格式

        try:
            return self.response_type(result)
        except (ValueError, TypeError):
            return result
