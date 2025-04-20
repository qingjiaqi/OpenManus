from typing import List

from pydantic import Field

from app.agent.toolcall import ToolCallAgent
from app.prompt.swe import SYSTEM_PROMPT
from app.tool import Bash, StrReplaceEditor, Terminate, ToolCollection


# SWEAgent 是一个自主 AI 程序员，用于直接与计算机交互以完成任务。
# 继承自 ToolCallAgent，支持工具调用和自然语言对话。
class SWEAgent(ToolCallAgent):
    """An agent that implements the SWEAgent paradigm for executing code and natural conversations."""

    # 代理名称，用于标识代理类型
    name: str = "swe"
    # 代理描述，说明代理的功能
    description: str = "an autonomous AI programmer that interacts directly with the computer to solve tasks."

    # 系统提示词，用于初始化代理行为
    system_prompt: str = SYSTEM_PROMPT
    # 下一步提示词，用于引导代理执行后续操作
    next_step_prompt: str = ""

    # 可用工具集合，包括 Bash 命令执行、字符串替换编辑器和终止工具
    available_tools: ToolCollection = ToolCollection(
        Bash(), StrReplaceEditor(), Terminate()
    )
    # 特殊工具名称列表，默认包含终止工具的名称
    special_tool_names: List[str] = Field(default_factory=lambda: [Terminate().name])

    # 最大执行步骤数，限制代理的执行次数
    max_steps: int = 20
