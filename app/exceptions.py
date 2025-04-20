# 工具错误异常类，当工具执行过程中遇到错误时抛出
class ToolError(Exception):
    """工具执行过程中遇到的错误。"""

    def __init__(self, message):
        # 初始化异常，设置错误消息
        self.message = message


# OpenManus 基础异常类，所有 OpenManus 相关异常的基类
class OpenManusError(Exception):
    """OpenManus 项目的所有异常的基类。"""


# 令牌限制超出异常类，当令牌数量超过限制时抛出
class TokenLimitExceeded(OpenManusError):
    """令牌数量超过限制时抛出的异常。"""
