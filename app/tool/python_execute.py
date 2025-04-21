# 导入必要的模块
import multiprocessing  # 用于多进程管理
import sys  # 用于系统相关操作
from io import StringIO  # 用于字符串IO操作
from typing import Dict  # 用于类型注解

from app.tool.base import BaseTool  # 导入基础工具类


class PythonExecute(BaseTool):
    """一个用于执行Python代码的工具，支持超时和安全限制。"""

    name: str = "python_execute"  # 工具名称
    description: str = "执行Python代码字符串。注意：仅打印输出可见，函数返回值不会被捕获。使用print语句查看结果。"  # 工具描述
    parameters: dict = {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "要执行的Python代码。",
            },
        },
        "required": ["code"],  # 必填参数
    }

    def _run_code(self, code: str, result_dict: dict, safe_globals: dict) -> None:
        """执行Python代码的核心方法。

        Args:
            code (str): 要执行的Python代码。
            result_dict (dict): 用于存储执行结果的字典。
            safe_globals (dict): 安全的全局变量环境。
        """
        original_stdout = sys.stdout  # 保存原始标准输出
        try:
            output_buffer = StringIO()  # 创建字符串缓冲区
            sys.stdout = output_buffer  # 重定向标准输出到缓冲区
            exec(code, safe_globals, safe_globals)  # 执行代码
            result_dict["observation"] = output_buffer.getvalue()  # 存储输出结果
            result_dict["success"] = True  # 标记执行成功
        except Exception as e:
            result_dict["observation"] = str(e)  # 存储异常信息
            result_dict["success"] = False  # 标记执行失败
        finally:
            sys.stdout = original_stdout  # 恢复原始标准输出

    async def execute(
        self,
        code: str,
        timeout: int = 5,
    ) -> Dict:
        """执行Python代码的异步方法，支持超时控制。

        Args:
            code (str): 要执行的Python代码。
            timeout (int): 超时时间（秒），默认为5秒。

        Returns:
            Dict: 包含执行结果或错误信息的字典，以及执行状态。
        """

        # 创建多进程管理器，用于进程间共享数据（如列表、字典等）
        with multiprocessing.Manager() as manager:
            result = manager.dict({"observation": "", "success": False})  # 创建共享字典
            if isinstance(__builtins__, dict):
                safe_globals = {"__builtins__": __builtins__}  # 设置安全的全局变量环境
            else:
                safe_globals = {"__builtins__": __builtins__.__dict__.copy()}  # 复制内置字典
            proc = multiprocessing.Process(
                target=self._run_code, args=(code, result, safe_globals)  # 创建子进程执行代码
            )
            proc.start()  # 启动子进程
            proc.join(timeout)  # 等待子进程完成或超时

            # 处理超时
            if proc.is_alive():
                proc.terminate()  # 终止子进程
                proc.join(1)  # 等待子进程完全退出
                return {
                    "observation": f"执行超时，超过 {timeout} 秒",  # 返回超时信息
                    "success": False,  # 标记执行失败
                }
            return dict(result)  # 返回执行结果


# 测试代码块
if __name__ == "__main__":
    import asyncio

    async def my_execute():
        tool = PythonExecute()
        result = await tool.execute(
            code="print('Hello, World!')\nprint(1+2)",
            timeout=5
        )
        print("测试结果:", result)

    asyncio.run(my_execute())
