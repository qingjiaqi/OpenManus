# 导入必要的模块
import asyncio  # 异步IO支持
import os  # 操作系统相关功能
from typing import Optional  # 类型注解支持

# 导入自定义异常和基础工具类
from app.exceptions import ToolError  # 工具执行异常
from app.tool.base import BaseTool, CLIResult  # 基础工具类和命令行结果类

# Bash工具的描述信息，用于说明工具的功能和使用方式
_BASH_DESCRIPTION = """Execute a bash command in the terminal.
* Long running commands: For commands that may run indefinitely, it should be run in the background and the output should be redirected to a file, e.g. command = `python3 app.py > server.log 2>&1 &`.
* Interactive: If a bash command returns exit code `-1`, this means the process is not yet finished. The assistant must then send a second call to terminal with an empty `command` (which will retrieve any additional logs), or it can send additional text (set `command` to the text) to STDIN of the running process, or it can send command=`ctrl+c` to interrupt the process.
* Timeout: If a command execution result says "Command timed out. Sending SIGINT to the process", the assistant should retry running the command in the background.
"""


class _BashSession:
    """一个Bash会话的封装类，用于管理Bash进程的启动、停止和命令执行。"""

    _started: bool  # 标记会话是否已启动
    _process: asyncio.subprocess.Process  # 异步子进程对象

    command: str = "/bin/bash"  # 默认的Bash命令路径
    _output_delay: float = 0.2  # 输出延迟（秒），用于控制读取输出的频率
    _timeout: float = 120.0  # 命令执行的超时时间（秒）
    _sentinel: str = "<<exit>>"  # 用于标记命令结束的哨兵字符串

    def __init__(self):
        """初始化Bash会话，标记为未启动状态。"""
        self._started = False
        self._timed_out = False  # 标记是否超时

    async def start(self):
        """启动Bash会话，创建异步子进程。"""
        if self._started:
            return  # 如果已启动，则直接返回

        # 创建异步子进程，配置标准输入、输出和错误流
        self._process = await asyncio.create_subprocess_shell(
            self.command,
            preexec_fn=os.setsid,  # 设置进程组ID
            shell=True,  # 使用Shell执行命令
            bufsize=0,  # 无缓冲
            stdin=asyncio.subprocess.PIPE,  # 标准输入管道
            stdout=asyncio.subprocess.PIPE,  # 标准输出管道
            stderr=asyncio.subprocess.PIPE,  # 标准错误管道
        )

        self._started = True  # 标记为已启动

    def stop(self):
        """终止Bash会话。"""
        if not self._started:
            raise ToolError("Session has not started.")  # 如果未启动，抛出异常
        if self._process.returncode is not None:
            return  # 如果进程已结束，直接返回
        self._process.terminate()  # 终止进程

    async def run(self, command: str):
        """在Bash会话中执行命令。"""
        if not self._started:
            raise ToolError("Session has not started.")  # 如果未启动，抛出异常
        if self._process.returncode is not None:
            return CLIResult(
                system="tool must be restarted",
                error=f"bash has exited with returncode {self._process.returncode}",
            )  # 如果进程已结束，返回需要重启的结果
        if self._timed_out:
            raise ToolError(
                f"timed out: bash has not returned in {self._timeout} seconds and must be restarted",
            )  # 如果超时，抛出异常

        # 确保标准输入、输出和错误流已正确配置
        assert self._process.stdin
        assert self._process.stdout
        assert self._process.stderr

        # 将命令和哨兵字符串写入标准输入
        self._process.stdin.write(
            command.encode() + f"; echo '{self._sentinel}'\n".encode()
        )
        await self._process.stdin.drain()  # 确保数据已发送

        # 读取命令输出，直到找到哨兵字符串
        try:
            async with asyncio.timeout(self._timeout):  # 设置超时
                while True:
                    await asyncio.sleep(self._output_delay)  # 延迟读取
                    # 从标准输出缓冲区读取数据
                    output = (
                        self._process.stdout._buffer.decode()
                    )  # pyright: ignore[reportAttributeAccessIssue]
                    if self._sentinel in output:  # 检查哨兵字符串
                        output = output[: output.index(self._sentinel)]  # 截取有效输出
                        break
        except asyncio.TimeoutError:  # 超时处理
            self._timed_out = True
            raise ToolError(
                f"timed out: bash has not returned in {self._timeout} seconds and must be restarted",
            ) from None

        # 清理输出末尾的换行符
        if output.endswith("\n"):
            output = output[:-1]

        # 读取标准错误输出并清理换行符
        error = (
            self._process.stderr._buffer.decode()
        )  # pyright: ignore[reportAttributeAccessIssue]
        if error.endswith("\n"):
            error = error[:-1]

        # 清空缓冲区，为下一次命令执行做准备
        self._process.stdout._buffer.clear()  # pyright: ignore[reportAttributeAccessIssue]
        self._process.stderr._buffer.clear()  # pyright: ignore[reportAttributeAccessIssue]

        return CLIResult(output=output, error=error)  # 返回命令执行结果


class Bash(BaseTool):
    """Bash工具类，继承自BaseTool，用于执行Bash命令。"""

    name: str = "bash"  # 工具名称
    description: str = _BASH_DESCRIPTION  # 工具描述
    parameters: dict = {  # 工具参数定义
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The bash command to execute. Can be empty to view additional logs when previous exit code is `-1`. Can be `ctrl+c` to interrupt the currently running process.",
            },
        },
        "required": ["command"],
    }

    _session: Optional[_BashSession] = None  # 当前Bash会话实例

    async def execute(
        self, command: str | None = None, restart: bool = False, **kwargs
    ) -> CLIResult:
        """执行Bash命令或重启会话。"""
        if restart:  # 如果需要重启
            if self._session:
                self._session.stop()  # 停止当前会话
            self._session = _BashSession()  # 创建新会话
            await self._session.start()  # 启动会话
            return CLIResult(system="tool has been restarted.")  # 返回重启结果

        if self._session is None:  # 如果会话未初始化
            self._session = _BashSession()  # 创建新会话
            await self._session.start()  # 启动会话

        if command is not None:  # 如果提供了命令
            return await self._session.run(command)  # 执行命令并返回结果

        raise ToolError("no command provided.")  # 如果未提供命令，抛出异常


if __name__ == "__main__":
    # 测试代码：执行`ls -l`命令并打印结果
    bash = Bash()
    rst = asyncio.run(bash.execute("ls -l"))
    print(rst)
