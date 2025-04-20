from abc import ABC, abstractmethod  # 导入抽象基类模块，用于定义抽象类和抽象方法
from typing import Dict, Optional, Protocol  # 导入类型注解模块，用于类型提示

from app.config import SandboxSettings  # 导入沙箱配置类，用于配置沙箱环境
from app.sandbox.core.sandbox import DockerSandbox  # 导入Docker沙箱实现类，用于管理Docker容器


class SandboxFileOperations(Protocol):
    """沙箱文件操作协议，定义文件操作的接口规范。"""

    async def copy_from(self, container_path: str, local_path: str) -> None:
        """从容器复制文件到本地。

        Args:
            container_path: 容器内文件路径。
            local_path: 本地目标路径。
        """
        ...

    async def copy_to(self, local_path: str, container_path: str) -> None:
        """从本地复制文件到容器。

        Args:
            local_path: 本地源文件路径。
            container_path: 容器内目标路径。
        """
        ...

    async def read_file(self, path: str) -> str:
        """从容器读取文件内容。

        Args:
            path: 容器内文件路径。

        Returns:
            str: 文件内容。
        """
        ...

    async def write_file(self, path: str, content: str) -> None:
        """向容器写入文件内容。

        Args:
            path: 容器内文件路径。
            content: 要写入的内容。
        """
        ...


class BaseSandboxClient(ABC):
    """沙箱客户端基类，定义沙箱操作的抽象接口。"""

    @abstractmethod
    async def create(
        self,
        config: Optional[SandboxSettings] = None,
        volume_bindings: Optional[Dict[str, str]] = None,
    ) -> None:
        """创建沙箱。

        Args:
            config: 沙箱配置。
            volume_bindings: 卷绑定映射。
        """
        ...

    @abstractmethod
    async def run_command(self, command: str, timeout: Optional[int] = None) -> str:
        """在沙箱中执行命令。

        Args:
            command: 要执行的命令。
            timeout: 执行超时时间（秒）。

        Returns:
            str: 命令输出。
        """
        ...

    @abstractmethod
    async def copy_from(self, container_path: str, local_path: str) -> None:
        """从容器复制文件到本地。

        Args:
            container_path: 容器内文件路径。
            local_path: 本地目标路径。
        """
        ...

    @abstractmethod
    async def copy_to(self, local_path: str, container_path: str) -> None:
        """从本地复制文件到容器。

        Args:
            local_path: 本地源文件路径。
            container_path: 容器内目标路径。
        """
        ...

    @abstractmethod
    async def read_file(self, path: str) -> str:
        """从容器读取文件内容。

        Args:
            path: 容器内文件路径。

        Returns:
            str: 文件内容。
        """
        ...

    @abstractmethod
    async def write_file(self, path: str, content: str) -> None:
        """向容器写入文件内容。

        Args:
            path: 容器内文件路径。
            content: 要写入的内容。
        """
        ...

    @abstractmethod
    async def cleanup(self) -> None:
        """清理沙箱资源。"""
        ...


class LocalSandboxClient(BaseSandboxClient):
    """本地沙箱客户端实现类，基于Docker沙箱。"""

    def __init__(self):
        """初始化本地沙箱客户端。"""
        self.sandbox: Optional[DockerSandbox] = None  # Docker沙箱实例

    async def create(
        self,
        config: Optional[SandboxSettings] = None,
        volume_bindings: Optional[Dict[str, str]] = None,
    ) -> None:
        """创建沙箱。

        Args:
            config: 沙箱配置。
            volume_bindings: 卷绑定映射。

        Raises:
            RuntimeError: 如果沙箱创建失败。
        """
        self.sandbox = DockerSandbox(config, volume_bindings)  # 创建Docker沙箱实例
        await self.sandbox.create()

    async def run_command(self, command: str, timeout: Optional[int] = None) -> str:
        """在沙箱中执行命令。

        Args:
            command: 要执行的命令。
            timeout: 执行超时时间（秒）。

        Returns:
            str: 命令输出。

        Raises:
            RuntimeError: 如果沙箱未初始化。
        """
        if not self.sandbox:
            raise RuntimeError("沙箱未初始化")
        return await self.sandbox.run_command(command, timeout)

    async def copy_from(self, container_path: str, local_path: str) -> None:
        """从容器复制文件到本地。

        Args:
            container_path: 容器内文件路径。
            local_path: 本地目标路径。

        Raises:
            RuntimeError: 如果沙箱未初始化。
        """
        if not self.sandbox:
            raise RuntimeError("沙箱未初始化")
        await self.sandbox.copy_from(container_path, local_path)

    async def copy_to(self, local_path: str, container_path: str) -> None:
        """从本地复制文件到容器。

        Args:
            local_path: 本地源文件路径。
            container_path: 容器内目标路径。

        Raises:
            RuntimeError: 如果沙箱未初始化。
        """
        if not self.sandbox:
            raise RuntimeError("沙箱未初始化")
        await self.sandbox.copy_to(local_path, container_path)

    async def read_file(self, path: str) -> str:
        """从容器读取文件内容。

        Args:
            path: 容器内文件路径。

        Returns:
            str: 文件内容。

        Raises:
            RuntimeError: 如果沙箱未初始化。
        """
        if not self.sandbox:
            raise RuntimeError("沙箱未初始化")
        return await self.sandbox.read_file(path)

    async def write_file(self, path: str, content: str) -> None:
        """向容器写入文件内容。

        Args:
            path: 容器内文件路径。
            content: 要写入的内容。

        Raises:
            RuntimeError: 如果沙箱未初始化。
        """
        if not self.sandbox:
            raise RuntimeError("沙箱未初始化")
        await self.sandbox.write_file(path, content)

    async def cleanup(self) -> None:
        """清理沙箱资源。"""
        if self.sandbox:
            await self.sandbox.cleanup()
            self.sandbox = None  # 重置沙箱实例


def create_sandbox_client() -> LocalSandboxClient:
    """创建沙箱客户端实例。

    Returns:
        LocalSandboxClient: 沙箱客户端实例。
    """
    return LocalSandboxClient()


SANDBOX_CLIENT = create_sandbox_client()  # 全局沙箱客户端实例