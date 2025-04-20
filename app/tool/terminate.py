# 导入基础工具类 BaseTool，用于定义工具的基础功能
from app.tool.base import BaseTool


# 工具的描述信息，用于说明工具的用途
_TERMINATE_DESCRIPTION = """Terminate the interaction when the request is met OR if the assistant cannot proceed further with the task.
When you have finished all the tasks, call this tool to end the work."""


# Terminate 工具类，继承自 BaseTool
class Terminate(BaseTool):
    # 工具名称
    name: str = "terminate"
    # 工具描述
    description: str = _TERMINATE_DESCRIPTION
    # 工具参数定义
    parameters: dict = {
        "type": "object",
        "properties": {
            "status": {
                "type": "string",
                "description": "The finish status of the interaction.",
                "enum": ["success", "failure"],
            }
        },
        "required": ["status"],
    }

    # 执行方法，用于结束当前交互
    async def execute(self, status: str) -> str:
        """
        结束当前交互并返回状态
        :param status: 交互的结束状态（"success" 或 "failure"）
        :return: 返回交互结束的状态信息
        """
        return f"The interaction has been completed with status: {status}"
