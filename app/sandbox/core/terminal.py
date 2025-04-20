"""
异步 Docker 终端模块

提供 Docker 容器的异步终端功能，支持交互式命令执行和超时控制。
"""

# 导入必要的库
import asyncio  # 异步 I/O 支持
import re  # 正则表达式
import socket  # 网络通信
from typing import Dict, Optional, Tuple, Union  # 类型注解

import docker  # Docker SDK
from docker import APIClient  # Docker API 客户端
from docker.errors import APIError  # Docker API 错误
from docker.models.containers import Container  # Docker 容器模型


class DockerSession:
    """
    Docker 会话类，用于管理单个容器的交互式终端会话。

    Args:
        container_id (str): Docker 容器的 ID。
    """
    def __init__(self, container_id: str) -> None:
        """初始化 Docker 会话。

        Args:
            container_id: Docker 容器的 ID。
        """
        self.api = APIClient()  # Docker API 客户端实例
        self.container_id = container_id  # 容器 ID
        self.exec_id = None  # 执行命令的 ID
        self.socket = None  # 网络套接字

    async def create(self, working_dir: str, env_vars: Dict[str, str]) -> None:
        """创建与容器的交互式会话。

        Args:
            working_dir: 容器内的工作目录。
            env_vars: 环境变量字典。

        Raises:
            RuntimeError: 如果套接字连接失败。
        """
        # 启动命令，设置工作目录和终端提示符
        startup_command = [
            "bash",
            "-c",
            f"cd {working_dir} && "
            "PROMPT_COMMAND='' "
            "PS1='$ ' "
            "exec bash --norc --noprofile",
        ]

        # 创建执行命令的实例
        exec_data = self.api.exec_create(
            self.container_id,
            startup_command,
            stdin=True,  # 允许标准输入
            tty=True,  # 分配伪终端
            stdout=True,  # 捕获标准输出
            stderr=True,  # 捕获标准错误
            privileged=True,  # 特权模式
            user="root",  # 以 root 用户运行
            environment={**env_vars, "TERM": "dumb", "PS1": "$ ", "PROMPT_COMMAND": ""},  # 设置环境变量
        )
        self.exec_id = exec_data["Id"]  # 保存执行 ID

        # 启动执行并获取套接字连接
        socket_data = self.api.exec_start(
            self.exec_id, socket=True, tty=True, stream=True, demux=True
        )

        # 检查套接字是否可用
        if hasattr(socket_data, "_sock"):
            self.socket = socket_data._sock
            self.socket.setblocking(False)  # 设置为非阻塞模式
        else:
            raise RuntimeError("Failed to get socket connection")

        # 读取输出直到出现提示符
        await self._read_until_prompt()

    async def close(self) -> None:
        """关闭会话并清理资源。

        1. 发送退出命令
        2. 关闭套接字连接
        3. 检查并清理执行实例
        """
        try:
            if self.socket:
                # 发送退出命令以关闭 bash 会话
                try:
                    self.socket.sendall(b"exit\n")
                    # 留出时间执行命令
                    await asyncio.sleep(0.1)
                except:
                    pass  # 忽略发送错误，继续清理

                # 关闭套接字连接
                try:
                    self.socket.shutdown(socket.SHUT_RDWR)
                except:
                    pass  # 某些平台可能不支持 shutdown

                self.socket.close()
                self.socket = None

            if self.exec_id:
                try:
                    # 检查执行实例状态
                    exec_inspect = self.api.exec_inspect(self.exec_id)
                    if exec_inspect.get("Running", False):
                        # 如果仍在运行，等待其完成
                        await asyncio.sleep(0.5)
                except:
                    pass  # 忽略检查错误，继续清理

                self.exec_id = None

        except Exception as e:
            # 记录错误但不中断清理
            print(f"Warning: Error during session cleanup: {e}")

    async def _read_until_prompt(self) -> str:
        """读取输出直到出现提示符。

        Returns:
            包含提示符前输出的字符串。

        Raises:
            socket.error: 如果套接字通信失败。
        """
        buffer = b""  # 缓冲区存储读取的数据
        while b"$ " not in buffer:  # 检查是否出现提示符
            try:
                chunk = self.socket.recv(4096)  # 读取数据块
                if chunk:
                    buffer += chunk  # 添加到缓冲区
            except socket.error as e:
                if e.errno == socket.EWOULDBLOCK:  # 非阻塞模式下无数据可读
                    await asyncio.sleep(0.1)  # 短暂等待后重试
                    continue
                raise  # 其他错误直接抛出
        return buffer.decode("utf-8")  # 返回解码后的字符串

    async def execute(self, command: str, timeout: Optional[int] = None) -> str:
        """执行命令并返回清理后的输出。

        Args:
            command: 要执行的 Shell 命令。
            timeout: 最大执行时间（秒）。

        Returns:
            清理后的命令输出字符串（移除提示符标记）。

        Raises:
            RuntimeError: 如果会话未初始化或执行失败。
            TimeoutError: 如果命令执行超时。
        """
        if not self.socket:
            raise RuntimeError("Session not initialized")

        try:
            # 清理命令以防止 Shell 注入
            sanitized_command = self._sanitize_command(command)
            full_command = f"{sanitized_command}\necho $?\n"  # 添加返回状态码命令
            self.socket.sendall(full_command.encode())  # 发送命令

            async def read_output() -> str:
                """异步读取命令输出。"""
                buffer = b""  # 缓冲区
                result_lines = []  # 存储结果行
                command_sent = False  # 标记命令是否已发送

                while True:
                    try:
                        chunk = self.socket.recv(4096)  # 读取数据块
                        if not chunk:
                            break  # 无数据时退出

                        buffer += chunk
                        lines = buffer.split(b"\n")  # 按行分割

                        buffer = lines[-1]  # 保留未完成的行
                        lines = lines[:-1]  # 处理完整的行

                        for line in lines:
                            line = line.rstrip(b"\r")  # 移除回车符

                            if not command_sent:
                                command_sent = True  # 标记命令已发送
                                continue

                            if line.strip() == b"echo $?" or line.strip().isdigit():
                                continue  # 跳过状态码行

                            if line.strip():
                                result_lines.append(line)  # 添加到结果

                        if buffer.endswith(b"$ "):  # 检查是否出现提示符
                            break

                    except socket.error as e:
                        if e.errno == socket.EWOULDBLOCK:
                            await asyncio.sleep(0.1)  # 短暂等待后重试
                            continue
                        raise

                output = b"\n".join(result_lines).decode("utf-8")  # 合并并解码
                output = re.sub(r"\n\$ echo \$\$?.*$", "", output)  # 移除状态码标记
                return output

            if timeout:
                result = await asyncio.wait_for(read_output(), timeout)  # 带超时的读取
            else:
                result = await read_output()  # 无超时的读取

            return result.strip()  # 返回清理后的输出

        except asyncio.TimeoutError:
            raise TimeoutError(f"Command execution timed out after {timeout} seconds")
        except Exception as e:
            raise RuntimeError(f"Failed to execute command: {e}")

    def _sanitize_command(self, command: str) -> str:
        """清理命令字符串以防止 Shell 注入。

        Args:
            command: 原始命令字符串。

        Returns:
            清理后的命令字符串。

        Raises:
            ValueError: 如果命令包含潜在危险模式。
        """
        # 检查危险命令列表
        risky_commands = [
            "rm -rf /",
            "rm -rf /*",
            "mkfs",
            "dd if=/dev/zero",
            ":(){:|:&};:",
            "chmod -R 777 /",
            "chown -R",
        ]

        for risky in risky_commands:
            if risky in command.lower():  # 忽略大小写检查
                raise ValueError(
                    f"Command contains potentially dangerous operation: {risky}"
                )

        return command  # 返回原始命令（未修改）


class AsyncDockerizedTerminal:
    """
    异步 Docker 终端类，封装了 Docker 容器的交互式终端功能。

    Args:
        container (Union[str, Container]): Docker 容器 ID 或 Container 对象。
        working_dir (str): 容器内的工作目录，默认为 "/workspace"。
        env_vars (Optional[Dict[str, str]]): 环境变量字典。
        default_timeout (int): 默认命令执行超时时间（秒）。
    """
    def __init__(
        self,
        container: Union[str, Container],
        working_dir: str = "/workspace",
        env_vars: Optional[Dict[str, str]] = None,
        default_timeout: int = 60,
    ) -> None:
        """初始化异步 Docker 终端。"""
        self.client = docker.from_env()  # Docker 客户端
        self.container = (
            container
            if isinstance(container, Container)
            else self.client.containers.get(container)  # 获取容器对象
        )
        self.working_dir = working_dir  # 工作目录
        self.env_vars = env_vars or {}  # 环境变量
        self.default_timeout = default_timeout  # 默认超时时间
        self.session = None  # Docker 会话实例

    async def init(self) -> None:
        """初始化终端环境。

        确保工作目录存在并创建交互式会话。

        Raises:
            RuntimeError: 如果初始化失败。
        """
        await self._ensure_workdir()  # 确保工作目录存在

        self.session = DockerSession(self.container.id)  # 创建会话
        await self.session.create(self.working_dir, self.env_vars)  # 初始化会话

    async def _ensure_workdir(self) -> None:
        """确保容器内的工作目录存在。

        Raises:
            RuntimeError: 如果目录创建失败。
        """
        try:
            await self._exec_simple(f"mkdir -p {self.working_dir}")  # 创建目录
        except APIError as e:
            raise RuntimeError(f"Failed to create working directory: {e}")

    async def _exec_simple(self, cmd: str) -> Tuple[int, str]:
        """使用 Docker 的 exec_run 执行简单命令。

        Args:
            cmd: 要执行的命令。

        Returns:
            包含退出码和输出的元组。
        """
        result = await asyncio.to_thread(
            self.container.exec_run, cmd, environment=self.env_vars
        )
        return result.exit_code, result.output.decode("utf-8")  # 返回结果

    async def run_command(self, cmd: str, timeout: Optional[int] = None) -> str:
        """在容器中运行命令（带超时）。

        Args:
            cmd: Shell 命令。
            timeout: 最大执行时间（秒）。

        Returns:
            命令输出字符串。

        Raises:
            RuntimeError: 如果终端未初始化。
        """
        if not self.session:
            raise RuntimeError("Terminal not initialized")

        return await self.session.execute(cmd, timeout=timeout or self.default_timeout)

    async def close(self) -> None:
        """关闭终端会话。"""
        if self.session:
            await self.session.close()  # 关闭会话

    async def __aenter__(self) -> "AsyncDockerizedTerminal":
        """异步上下文管理器入口。"""
        await self.init()  # 初始化
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """异步上下文管理器退出。"""
        await self.close()  # 关闭会话