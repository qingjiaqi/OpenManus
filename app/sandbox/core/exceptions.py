"""Exception classes for the sandbox system.

This module defines custom exceptions used throughout the sandbox system to
handle various error conditions in a structured way.
"""
# 沙箱系统的异常类模块
# 定义了一系列自定义异常，用于以结构化方式处理沙箱系统中的各种错误场景


class SandboxError(Exception):
    """Base exception for sandbox-related errors."""
    # 沙箱相关错误的基类异常，所有沙箱自定义异常均继承此类


class SandboxTimeoutError(SandboxError):
    """Exception raised when a sandbox operation times out."""
    # 沙箱操作超时时抛出的异常


class SandboxResourceError(SandboxError):
    """Exception raised for resource-related errors."""
    # 沙箱资源相关错误（如内存不足、文件权限等）时抛出的异常