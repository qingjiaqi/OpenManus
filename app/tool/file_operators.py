"""
文件操作接口及其实现，支持本地和沙箱环境。
"""

import asyncio  # 异步IO库，用于支持异步文件操作
from pathlib import Path  # 路径操作库，提供跨平台路径处理
from typing import Optional, Protocol, Tuple, Union, runtime_checkable  # 类型注解支持

from app.config import SandboxSettings  # 沙箱配置
from app.exceptions import ToolError  # 自定义异常
from app.sandbox.client import SANDBOX_CLIENT  # 沙箱客户端


# 定义路径类型，支持字符串或Path对象
PathLike = Union[str, Path]


@runtime_checkable
class FileOperator(Protocol):
    """
    文件操作接口协议，定义不同环境下的文件操作行为。
    """

    async def read_file(self, path: PathLike) -> str:
        """
        从文件中读取内容。
        :param path: 文件路径
        :return: 文件内容字符串
        """
        ...

    async def write_file(self, path: PathLike, content: str) -> None:
        """
        将内容写入文件。
        :param path: 文件路径
        :param content: 写入的内容
        """
        ...

    async def is_directory(self, path: PathLike) -> bool:
        """
        检查路径是否为目录。
        :param path: 路径
        :return: 是否为目录
        """
        ...

    async def exists(self, path: PathLike) -> bool:
        """
        检查路径是否存在。
        :param path: 路径
        :return: 路径是否存在
        """
        ...

    async def run_command(
        self, cmd: str, timeout: Optional[float] = 120.0
    ) -> Tuple[int, str, str]:
        """
        执行Shell命令并返回结果。
        :param cmd: 命令字符串
        :param timeout: 超时时间（秒）
        :return: (返回码, 标准输出, 标准错误)
        """
        ...


class LocalFileOperator(FileOperator):
    """
    本地文件系统操作实现类。
    """

    encoding: str = "utf-8"  # 文件编码格式

    async def read_file(self, path: PathLike) -> str:
        """
        从本地文件读取内容。
        :param path: 文件路径
        :return: 文件内容字符串
        :raises ToolError: 读取失败时抛出异常
        """
        try:
            return Path(path).read_text(encoding=self.encoding)
        except Exception as e:
            raise ToolError(f"Failed to read {path}: {str(e)}") from None

    async def write_file(self, path: PathLike, content: str) -> None:
        """
        将内容写入本地文件。
        :param path: 文件路径
        :param content: 写入的内容
        :raises ToolError: 写入失败时抛出异常
        """
        try:
            Path(path).write_text(content, encoding=self.encoding)
        except Exception as e:
            raise ToolError(f"Failed to write to {path}: {str(e)}") from None

    async def is_directory(self, path: PathLike) -> bool:
        """
        检查路径是否为目录。
        :param path: 路径
        :return: 是否为目录
        """
        return Path(path).is_dir()

    async def exists(self, path: PathLike) -> bool:
        """
        检查路径是否存在。
        :param path: 路径
        :return: 路径是否存在
        """
        return Path(path).exists()

    async def run_command(
        self, cmd: str, timeout: Optional[float] = 120.0
    ) -> Tuple[int, str, str]:
        """
        在本地执行Shell命令。
        :param cmd: 命令字符串
        :param timeout: 超时时间（秒）
        :return: (返回码, 标准输出, 标准错误)
        :raises TimeoutError: 命令执行超时时抛出异常
        """
        process = await asyncio.create_subprocess_shell(
            cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=timeout
            )
            return (
                process.returncode or 0,
                stdout.decode(),
                stderr.decode(),
            )
        except asyncio.TimeoutError as exc:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            raise TimeoutError(
                f"Command '{cmd}' timed out after {timeout} seconds"
            ) from exc


class SandboxFileOperator(FileOperator):
    """
    沙箱环境文件操作实现类。
    """

    def __init__(self):
        self.sandbox_client = SANDBOX_CLIENT  # 沙箱客户端实例

    async def _ensure_sandbox_initialized(self):
        """
        确保沙箱已初始化。
        :raises ToolError: 初始化失败时抛出异常
        """
        if not self.sandbox_client.sandbox:
            await self.sandbox_client.create(config=SandboxSettings())

    async def read_file(self, path: PathLike) -> str:
        """
        从沙箱文件读取内容。
        :param path: 文件路径
        :return: 文件内容字符串
        :raises ToolError: 读取失败时抛出异常
        """
        await self._ensure_sandbox_initialized()
        try:
            return await self.sandbox_client.read_file(str(path))
        except Exception as e:
            raise ToolError(f"Failed to read {path} in sandbox: {str(e)}") from None

    async def write_file(self, path: PathLike, content: str) -> None:
        """
        将内容写入沙箱文件。
        :param path: 文件路径
        :param content: 写入的内容
        :raises ToolError: 写入失败时抛出异常
        """
        await self._ensure_sandbox_initialized()
        try:
            await self.sandbox_client.write_file(str(path), content)
        except Exception as e:
            raise ToolError(f"Failed to write to {path} in sandbox: {str(e)}") from None

    async def is_directory(self, path: PathLike) -> bool:
        """
        检查沙箱路径是否为目录。
        :param path: 路径
        :return: 是否为目录
        """
        await self._ensure_sandbox_initialized()
        result = await self.sandbox_client.run_command(
            f"test -d {path} && echo 'true' || echo 'false'"
        )
        return result.strip() == "true"

    async def exists(self, path: PathLike) -> bool:
        """
        检查沙箱路径是否存在。
        :param path: 路径
        :return: 路径是否存在
        """
        await self._ensure_sandbox_initialized()
        result = await self.sandbox_client.run_command(
            f"test -e {path} && echo 'true' || echo 'false'"
        )
        return result.strip() == "true"

    async def run_command(
        self, cmd: str, timeout: Optional[float] = 120.0
    ) -> Tuple[int, str, str]:
        """
        在沙箱中执行Shell命令。
        :param cmd: 命令字符串
        :param timeout: 超时时间（秒）
        :return: (返回码, 标准输出, 标准错误)
        :raises TimeoutError: 命令执行超时时抛出异常
        """
        await self._ensure_sandbox_initialized()
        try:
            stdout = await self.sandbox_client.run_command(
                cmd, timeout=int(timeout) if timeout else None
            )
            return (
                0,  # 沙箱实现中默认返回0
                stdout,
                "",  # 沙箱实现中未捕获标准错误
            )
        except TimeoutError as exc:
            raise TimeoutError(
                f"Command '{cmd}' timed out after {timeout} seconds in sandbox"
            ) from exc
        except Exception as exc:
            return 1, "", f"Error executing command in sandbox: {str(exc)}"
