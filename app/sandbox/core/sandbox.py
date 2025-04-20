import asyncio  # 异步IO库，用于支持异步操作
import io  # 输入输出流操作库
import os  # 操作系统接口库
import tarfile  # 用于处理tar文件
import tempfile  # 用于创建临时文件和目录
import uuid  # 生成唯一标识符
from typing import Dict, Optional  # 类型注解支持

import docker  # Docker SDK
from docker.errors import NotFound  # Docker异常类
from docker.models.containers import Container  # Docker容器模型

from app.config import SandboxSettings  # 沙箱配置类
from app.sandbox.core.exceptions import SandboxTimeoutError  # 沙箱超时异常
from app.sandbox.core.terminal import AsyncDockerizedTerminal  # 异步Docker终端接口


class DockerSandbox:
    """Docker沙箱环境。

    提供容器化的执行环境，支持资源限制、文件操作和命令执行功能。

    属性:
        config: 沙箱配置。
        volume_bindings: 卷映射配置。
        client: Docker客户端实例。
        container: Docker容器实例。
        terminal: 容器终端接口。
    """

    def __init__(
        self,
        config: Optional[SandboxSettings] = None,
        volume_bindings: Optional[Dict[str, str]] = None,
    ):
        """初始化沙箱实例。

        参数:
            config: 沙箱配置，默认为None时使用默认配置。
            volume_bindings: 卷映射字典，格式为{主机路径: 容器路径}。
        """
        self.config = config or SandboxSettings()  # 初始化沙箱配置
        self.volume_bindings = volume_bindings or {}  # 初始化卷映射
        self.client = docker.from_env()  # 创建Docker客户端
        self.container: Optional[Container] = None  # 容器实例初始化为None
        self.terminal: Optional[AsyncDockerizedTerminal] = None  # 终端接口初始化为None

    async def create(self) -> "DockerSandbox":
        """创建并启动沙箱容器。

        返回:
            当前沙箱实例。

        异常:
            docker.errors.APIError: Docker API调用失败时抛出。
            RuntimeError: 容器创建或启动失败时抛出。
        """
        try:
            # 准备容器配置
            host_config = self.client.api.create_host_config(
                mem_limit=self.config.memory_limit,  # 内存限制
                cpu_period=100000,  # CPU周期
                cpu_quota=int(100000 * self.config.cpu_limit),  # CPU配额
                network_mode="none" if not self.config.network_enabled else "bridge",  # 网络模式
                binds=self._prepare_volume_bindings(),  # 卷绑定配置
            )

            # 生成唯一的容器名称
            container_name = f"sandbox_{uuid.uuid4().hex[:8]}"

            # 创建容器
            container = await asyncio.to_thread(
                self.client.api.create_container,
                image=self.config.image,  # 容器镜像
                command="tail -f /dev/null",  # 容器启动命令
                hostname="sandbox",  # 容器主机名
                working_dir=self.config.work_dir,  # 工作目录
                host_config=host_config,  # 主机配置
                name=container_name,  # 容器名称
                tty=True,  # 分配伪终端
                detach=True,  # 后台运行
            )

            self.container = self.client.containers.get(container["Id"])  # 获取容器实例

            # 启动容器
            await asyncio.to_thread(self.container.start)

            # 初始化终端
            self.terminal = AsyncDockerizedTerminal(
                container["Id"],
                self.config.work_dir,
                env_vars={"PYTHONUNBUFFERED": "1"}  # 禁用Python输出缓冲
            )
            await self.terminal.init()

            return self

        except Exception as e:
            await self.cleanup()  # 清理资源
            raise RuntimeError(f"Failed to create sandbox: {e}") from e

    def _prepare_volume_bindings(self) -> Dict[str, Dict[str, str]]:
        """准备卷绑定配置。

        返回:
            卷绑定配置字典。
        """
        bindings = {}

        # 创建工作目录映射
        work_dir = self._ensure_host_dir(self.config.work_dir)
        bindings[work_dir] = {"bind": self.config.work_dir, "mode": "rw"}  # 读写模式

        # 添加自定义卷绑定
        for host_path, container_path in self.volume_bindings.items():
            bindings[host_path] = {"bind": container_path, "mode": "rw"}  # 读写模式

        return bindings

    @staticmethod
    def _ensure_host_dir(path: str) -> str:
        """确保主机目录存在。

        参数:
            path: 目录路径。

        返回:
            主机上的实际路径。
        """
        host_path = os.path.join(
            tempfile.gettempdir(),
            f"sandbox_{os.path.basename(path)}_{os.urandom(4).hex()}",
        )
        os.makedirs(host_path, exist_ok=True)  # 创建目录
        return host_path

    async def run_command(self, cmd: str, timeout: Optional[int] = None) -> str:
        """在沙箱中执行命令。

        参数:
            cmd: 要执行的命令。
            timeout: 超时时间（秒）。

        返回:
            命令输出字符串。

        异常:
            RuntimeError: 沙箱未初始化或命令执行失败时抛出。
            TimeoutError: 命令执行超时时抛出。
        """
        if not self.terminal:
            raise RuntimeError("Sandbox not initialized")

        try:
            return await self.terminal.run_command(
                cmd, timeout=timeout or self.config.timeout
            )
        except TimeoutError:
            raise SandboxTimeoutError(
                f"Command execution timed out after {timeout or self.config.timeout} seconds"
            )

    async def read_file(self, path: str) -> str:
        """从容器中读取文件。

        参数:
            path: 文件路径。

        返回:
            文件内容字符串。

        异常:
            FileNotFoundError: 文件不存在时抛出。
            RuntimeError: 读取操作失败时抛出。
        """
        if not self.container:
            raise RuntimeError("Sandbox not initialized")

        try:
            # 获取文件归档
            resolved_path = self._safe_resolve_path(path)
            tar_stream, _ = await asyncio.to_thread(
                self.container.get_archive, resolved_path
            )

            # 从tar流中读取文件内容
            content = await self._read_from_tar(tar_stream)
            return content.decode("utf-8")

        except NotFound:
            raise FileNotFoundError(f"File not found: {path}")
        except Exception as e:
            raise RuntimeError(f"Failed to read file: {e}")

    async def write_file(self, path: str, content: str) -> None:
        """向容器中的文件写入内容。

        参数:
            path: 目标路径。
            content: 文件内容。

        异常:
            RuntimeError: 写入操作失败时抛出。
        """
        if not self.container:
            raise RuntimeError("Sandbox not initialized")

        try:
            resolved_path = self._safe_resolve_path(path)
            parent_dir = os.path.dirname(resolved_path)

            # 创建父目录
            if parent_dir:
                await self.run_command(f"mkdir -p {parent_dir}")

            # 准备文件数据
            tar_stream = await self._create_tar_stream(
                os.path.basename(path), content.encode("utf-8")
            )

            # 写入文件
            await asyncio.to_thread(
                self.container.put_archive, parent_dir or "/", tar_stream
            )

        except Exception as e:
            raise RuntimeError(f"Failed to write file: {e}")

    def _safe_resolve_path(self, path: str) -> str:
        """安全解析容器路径，防止路径遍历攻击。

        参数:
            path: 原始路径。

        返回:
            解析后的绝对路径。

        异常:
            ValueError: 路径包含潜在不安全模式时抛出。
        """
        # 检查路径遍历尝试
        if ".." in path.split("/"):
            raise ValueError("Path contains potentially unsafe patterns")

        resolved = (
            os.path.join(self.config.work_dir, path)
            if not os.path.isabs(path)
            else path
        )
        return resolved

    async def copy_from(self, src_path: str, dst_path: str) -> None:
        """从容器中复制文件。

        参数:
            src_path: 源文件路径（容器内）。
            dst_path: 目标路径（主机）。

        异常:
            FileNotFoundError: 源文件不存在时抛出。
            RuntimeError: 复制操作失败时抛出。
        """
        try:
            # 确保目标文件的父目录存在
            parent_dir = os.path.dirname(dst_path)
            if parent_dir:
                os.makedirs(parent_dir, exist_ok=True)

            # 获取文件流
            resolved_src = self._safe_resolve_path(src_path)
            stream, stat = await asyncio.to_thread(
                self.container.get_archive, resolved_src
            )

            # 创建临时目录以提取文件
            with tempfile.TemporaryDirectory() as tmp_dir:
                # 将流写入临时文件
                tar_path = os.path.join(tmp_dir, "temp.tar")
                with open(tar_path, "wb") as f:
                    for chunk in stream:
                        f.write(chunk)

                # 提取文件
                with tarfile.open(tar_path) as tar:
                    members = tar.getmembers()
                    if not members:
                        raise FileNotFoundError(f"Source file is empty: {src_path}")

                    # 如果目标是目录，保留相对路径结构
                    if os.path.isdir(dst_path):
                        tar.extractall(dst_path)
                    else:
                        # 如果目标是文件，仅提取源文件内容
                        if len(members) > 1:
                            raise RuntimeError(
                                f"Source path is a directory but destination is a file: {src_path}"
                            )

                        with open(dst_path, "wb") as dst:
                            src_file = tar.extractfile(members[0])
                            if src_file is None:
                                raise RuntimeError(
                                    f"Failed to extract file: {src_path}"
                                )
                            dst.write(src_file.read())

        except docker.errors.NotFound:
            raise FileNotFoundError(f"Source file not found: {src_path}")
        except Exception as e:
            raise RuntimeError(f"Failed to copy file: {e}")

    async def copy_to(self, src_path: str, dst_path: str) -> None:
        """向容器中复制文件。

        参数:
            src_path: 源文件路径（主机）。
            dst_path: 目标路径（容器内）。

        异常:
            FileNotFoundError: 源文件不存在时抛出。
            RuntimeError: 复制操作失败时抛出。
        """
        try:
            if not os.path.exists(src_path):
                raise FileNotFoundError(f"Source file not found: {src_path}")

            # 在容器中创建目标目录
            resolved_dst = self._safe_resolve_path(dst_path)
            container_dir = os.path.dirname(resolved_dst)
            if container_dir:
                await self.run_command(f"mkdir -p {container_dir}")

            # 创建tar文件以上传
            with tempfile.TemporaryDirectory() as tmp_dir:
                tar_path = os.path.join(tmp_dir, "temp.tar")
                with tarfile.open(tar_path, "w") as tar:
                    # 处理目录源路径
                    if os.path.isdir(src_path):
                        os.path.basename(src_path.rstrip("/"))
                        for root, _, files in os.walk(src_path):
                            for file in files:
                                file_path = os.path.join(root, file)
                                arcname = os.path.join(
                                    os.path.basename(dst_path),
                                    os.path.relpath(file_path, src_path),
                                )
                                tar.add(file_path, arcname=arcname)
                    else:
                        # 添加单个文件到tar
                        tar.add(src_path, arcname=os.path.basename(dst_path))

                # 读取tar文件内容
                with open(tar_path, "rb") as f:
                    data = f.read()

                # 上传到容器
                await asyncio.to_thread(
                    self.container.put_archive,
                    os.path.dirname(resolved_dst) or "/",
                    data,
                )

                # 验证文件是否成功创建
                try:
                    await self.run_command(f"test -e {resolved_dst}")
                except Exception:
                    raise RuntimeError(f"Failed to verify file creation: {dst_path}")

        except FileNotFoundError:
            raise
        except Exception as e:
            raise RuntimeError(f"Failed to copy file: {e}")

    @staticmethod
    async def _create_tar_stream(name: str, content: bytes) -> io.BytesIO:
        """创建tar文件流。

        参数:
            name: 文件名。
            content: 文件内容。

        返回:
            tar文件流。
        """
        tar_stream = io.BytesIO()
        with tarfile.open(fileobj=tar_stream, mode="w") as tar:
            tarinfo = tarfile.TarInfo(name=name)
            tarinfo.size = len(content)
            tar.addfile(tarinfo, io.BytesIO(content))
        tar_stream.seek(0)
        return tar_stream

    @staticmethod
    async def _read_from_tar(tar_stream) -> bytes:
        """从tar流中读取文件内容。

        参数:
            tar_stream: tar文件流。

        返回:
            文件内容。

        异常:
            RuntimeError: 读取操作失败时抛出。
        """
        with tempfile.NamedTemporaryFile() as tmp:
            for chunk in tar_stream:
                tmp.write(chunk)
            tmp.seek(0)

            with tarfile.open(fileobj=tmp) as tar:
                member = tar.next()
                if not member:
                    raise RuntimeError("Empty tar archive")

                file_content = tar.extractfile(member)
                if not file_content:
                    raise RuntimeError("Failed to extract file content")

                return file_content.read()

    async def cleanup(self) -> None:
        """清理沙箱资源。"""
        errors = []
        try:
            if self.terminal:
                try:
                    await self.terminal.close()
                except Exception as e:
                    errors.append(f"Terminal cleanup error: {e}")
                finally:
                    self.terminal = None

            if self.container:
                try:
                    await asyncio.to_thread(self.container.stop, timeout=5)
                except Exception as e:
                    errors.append(f"Container stop error: {e}")

                try:
                    await asyncio.to_thread(self.container.remove, force=True)
                except Exception as e:
                    errors.append(f"Container remove error: {e}")
                finally:
                    self.container = None

        except Exception as e:
            errors.append(f"General cleanup error: {e}")

        if errors:
            print(f"Warning: Errors during cleanup: {', '.join(errors)}")

    async def __aenter__(self) -> "DockerSandbox":
        """异步上下文管理器入口。"""
        return await self.create()

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """异步上下文管理器退出。"""
        await self.cleanup()
