"""
文件和目录操作工具，支持沙箱环境。
"""

from collections import defaultdict
from pathlib import Path
from typing import Any, DefaultDict, List, Literal, Optional, get_args

from app.config import config
from app.exceptions import ToolError
from app.tool import BaseTool
from app.tool.base import CLIResult, ToolResult
from app.tool.file_operators import (
    FileOperator,
    LocalFileOperator,
    PathLike,
    SandboxFileOperator,
)


# 定义支持的编辑命令
Command = Literal[
    "view",  # 查看文件或目录内容
    "create",  # 创建文件
    "str_replace",  # 替换字符串
    "insert",  # 插入内容
    "undo_edit",  # 撤销编辑
]

# 常量定义
SNIPPET_LINES: int = 4  # 显示编辑片段时的上下文行数
MAX_RESPONSE_LEN: int = 16000  # 最大响应长度
TRUNCATED_MESSAGE: str = (
    "<response clipped><NOTE>To save on context only part of this file has been shown to you. "
    "You should retry this tool after you have searched inside the file with `grep -n` "
    "in order to find the line numbers of what you are looking for.</NOTE>"
)

# 工具描述
_STR_REPLACE_EDITOR_DESCRIPTION = """
自定义编辑工具，用于查看、创建和编辑文件
* 状态在命令调用和用户讨论之间持久化
* 如果 `path` 是文件，`view` 显示 `cat -n` 的结果；如果是目录，`view` 列出非隐藏文件和目录（最多2层）
* `create` 命令不能用于已存在的文件
* 如果命令生成过长输出，会被截断并标记为 `<response clipped>`
* `undo_edit` 命令会撤销对文件 `path` 的最后一次编辑

`str_replace` 命令的使用说明：
* `old_str` 参数必须完全匹配文件中的一行或多行连续内容（注意空格！）
* 如果 `old_str` 在文件中不唯一，替换不会执行。确保 `old_str` 包含足够的上下文以使其唯一
* `new_str` 参数包含替换后的内容
"""


def maybe_truncate(
    content: str, truncate_after: Optional[int] = MAX_RESPONSE_LEN
) -> str:
    """
    截断内容并在超过指定长度时附加提示信息。
    :param content: 待截断的内容
    :param truncate_after: 截断长度，默认为 MAX_RESPONSE_LEN
    :return: 截断后的内容或原内容
    """
    if not truncate_after or len(content) <= truncate_after:
        return content
    return content[:truncate_after] + TRUNCATED_MESSAGE


class StrReplaceEditor(BaseTool):
    """
    文件编辑工具，支持查看、创建、编辑文件，并提供沙箱支持。
    """

    name: str = "str_replace_editor"  # 工具名称
    description: str = _STR_REPLACE_EDITOR_DESCRIPTION  # 工具描述
    parameters: dict = {
        "type": "object",
        "properties": {
            "command": {
                "description": "要运行的命令。可选值：`view`, `create`, `str_replace`, `insert`, `undo_edit`。",
                "enum": ["view", "create", "str_replace", "insert", "undo_edit"],
                "type": "string",
            },
            "path": {
                "description": "文件或目录的绝对路径。",
                "type": "string",
            },
            "file_text": {
                "description": "`create` 命令的必需参数，表示要创建的文件内容。",
                "type": "string",
            },
            "old_str": {
                "description": "`str_replace` 命令的必需参数，表示要替换的字符串。",
                "type": "string",
            },
            "new_str": {
                "description": "`str_replace` 或 `insert` 命令的参数，表示替换或插入的新字符串。",
                "type": "string",
            },
            "insert_line": {
                "description": "`insert` 命令的必需参数，表示插入的行号（从1开始）。",
                "type": "integer",
            },
            "view_range": {
                "description": "`view` 命令的可选参数，表示查看的行范围（如 [11, 12] 显示第11和12行）。",
                "items": {"type": "integer"},
                "type": "array",
            },
        },
        "required": ["command", "path"],
    }
    _file_history: DefaultDict[PathLike, List[str]] = defaultdict(list)  # 文件编辑历史记录
    _local_operator: LocalFileOperator = LocalFileOperator()  # 本地文件操作器
    _sandbox_operator: SandboxFileOperator = SandboxFileOperator()  # 沙箱文件操作器

    # def _get_operator(self, use_sandbox: bool) -> FileOperator:
    def _get_operator(self) -> FileOperator:
        """
        根据配置返回文件操作器（本地或沙箱）。
        :return: 文件操作器实例
        """
        return (
            self._sandbox_operator
            if config.sandbox.use_sandbox
            else self._local_operator
        )

    async def execute(
        self,
        *,
        command: Command,
        path: str,
        file_text: str | None = None,
        view_range: list[int] | None = None,
        old_str: str | None = None,
        new_str: str | None = None,
        insert_line: int | None = None,
        **kwargs: Any,
    ) -> str:
        """
        执行文件操作命令。
        :param command: 命令类型
        :param path: 文件或目录路径
        :param file_text: 文件内容（用于 `create` 命令）
        :param view_range: 查看的行范围（用于 `view` 命令）
        :param old_str: 要替换的字符串（用于 `str_replace` 命令）
        :param new_str: 新字符串（用于 `str_replace` 或 `insert` 命令）
        :param insert_line: 插入的行号（用于 `insert` 命令）
        :param kwargs: 其他参数
        :return: 操作结果
        """
        # 获取文件操作器
        operator = self._get_operator()

        # 验证路径和命令的合法性
        await self.validate_path(command, Path(path), operator)

        # 执行命令
        if command == "view":
            result = await self.view(path, view_range, operator)
        elif command == "create":
            if file_text is None:
                raise ToolError("`create` 命令需要 `file_text` 参数")
            await operator.write_file(path, file_text)
            self._file_history[path].append(file_text)
            result = ToolResult(output=f"文件创建成功：{path}")
        elif command == "str_replace":
            if old_str is None:
                raise ToolError("`str_replace` 命令需要 `old_str` 参数")
            result = await self.str_replace(path, old_str, new_str, operator)
        elif command == "insert":
            if insert_line is None:
                raise ToolError("`insert` 命令需要 `insert_line` 参数")
            if new_str is None:
                raise ToolError("`insert` 命令需要 `new_str` 参数")
            result = await self.insert(path, insert_line, new_str, operator)
        elif command == "undo_edit":
            result = await self.undo_edit(path, operator)
        else:
            raise ToolError(
                f'无效命令 {command}。支持的命令：{get_args(Command)}'
            )

        return str(result)

    async def validate_path(
        self, command: str, path: Path, operator: FileOperator
    ) -> None:
        """
        验证路径和命令的合法性。
        :param command: 命令类型
        :param path: 文件或目录路径
        :param operator: 文件操作器
        :raises ToolError: 如果路径或命令不合法
        """
        # 检查路径是否为绝对路径
        if not path.is_absolute():
            raise ToolError(f"路径 {path} 不是绝对路径")

        # 非 `create` 命令需检查路径是否存在
        if command != "create":
            if not await operator.exists(path):
                raise ToolError(f"路径 {path} 不存在")

            # 检查路径是否为目录
            is_dir = await operator.is_directory(path)
            if is_dir and command != "view":
                raise ToolError(f"路径 {path} 是目录，仅支持 `view` 命令")

        # `create` 命令需确保文件不存在
        elif command == "create":
            exists = await operator.exists(path)
            if exists:
                raise ToolError(f"文件已存在：{path}，无法覆盖")

    async def view(
        self,
        path: PathLike,
        view_range: Optional[List[int]] = None,
        operator: FileOperator = None,
    ) -> CLIResult:
        """
        查看文件或目录内容。
        :param path: 文件或目录路径
        :param view_range: 查看的行范围
        :param operator: 文件操作器
        :return: 查看结果
        """
        # 检查是否为目录
        is_dir = await operator.is_directory(path)

        if is_dir:
            # 目录不支持行范围参数
            if view_range:
                raise ToolError("`view_range` 参数不能用于目录")
            return await self._view_directory(path, operator)
        else:
            return await self._view_file(path, operator, view_range)

    @staticmethod
    async def _view_directory(path: PathLike, operator: FileOperator) -> CLIResult:
        """
        查看目录内容。
        :param path: 目录路径
        :param operator: 文件操作器
        :return: 目录内容
        """
        find_cmd = f"find {path} -maxdepth 2 -not -path '*/\.*'"
        returncode, stdout, stderr = await operator.run_command(find_cmd)

        if not stderr:
            stdout = (
                f"目录 {path} 的内容（最多2层，排除隐藏项）：\n{stdout}\n"
            )

        return CLIResult(output=stdout, error=stderr)

    async def _view_file(
        self,
        path: PathLike,
        operator: FileOperator,
        view_range: Optional[List[int]] = None,
    ) -> CLIResult:
        """
        查看文件内容，支持行范围。
        :param path: 文件路径
        :param operator: 文件操作器
        :param view_range: 查看的行范围
        :return: 文件内容
        """
        file_content = await operator.read_file(path)
        init_line = 1

        # 处理行范围
        if view_range:
            if len(view_range) != 2 or not all(isinstance(i, int) for i in view_range):
                raise ToolError("`view_range` 应为两个整数的列表")

            file_lines = file_content.split("\n")
            n_lines_file = len(file_lines)
            init_line, final_line = view_range

            # 验证行范围
            if init_line < 1 or init_line > n_lines_file:
                raise ToolError(
                    f"`view_range` 起始行 {init_line} 超出文件范围 [1, {n_lines_file}]"
                )
            if final_line > n_lines_file:
                raise ToolError(
                    f"`view_range` 结束行 {final_line} 超出文件范围 [1, {n_lines_file}]"
                )
            if final_line != -1 and final_line < init_line:
                raise ToolError(
                    f"`view_range` 结束行 {final_line} 必须大于等于起始行 {init_line}"
                )

            # 应用行范围
            if final_line == -1:
                file_content = "\n".join(file_lines[init_line - 1 :])
            else:
                file_content = "\n".join(file_lines[init_line - 1 : final_line])

        # 格式化输出
        return CLIResult(
            output=self._make_output(file_content, str(path), init_line=init_line)
        )

    async def str_replace(
        self,
        path: PathLike,
        old_str: str,
        new_str: Optional[str] = None,
        operator: FileOperator = None,
    ) -> CLIResult:
        """
        替换文件中的唯一字符串。

        参数:
            path: 文件路径。
            old_str: 待替换的字符串。
            new_str: 替换后的字符串，可选。
            operator: 文件操作器，可选。

        返回值:
            CLIResult: 操作结果。
        """
        # 读取文件内容并扩展制表符
        file_content = (await operator.read_file(path)).expandtabs()
        old_str = old_str.expandtabs()
        new_str = new_str.expandtabs() if new_str is not None else ""

        # 检查 old_str 是否在文件中唯一
        occurrences = file_content.count(old_str)
        if occurrences == 0:
            raise ToolError(
                f"未执行替换，`old_str` `{old_str}` 在 {path} 中未出现。"
            )
        elif occurrences > 1:
            # 找到所有出现 old_str 的行号
            file_content_lines = file_content.split("\n")
            lines = [
                idx + 1
                for idx, line in enumerate(file_content_lines)
                if old_str in line
            ]
            raise ToolError(
                f"未执行替换。`old_str` `{old_str}` 在行 {lines} 中出现多次。请确保它是唯一的"
            )

        # 替换 old_str 为 new_str
        new_file_content = file_content.replace(old_str, new_str)

        # 将新内容写入文件
        await operator.write_file(path, new_file_content)

        # 将原始内容保存到历史记录
        self._file_history[path].append(file_content)

        # 生成编辑部分的代码片段
        replacement_line = file_content.split(old_str)[0].count("\n")
        start_line = max(0, replacement_line - SNIPPET_LINES)
        end_line = replacement_line + SNIPPET_LINES + new_str.count("\n")
        snippet = "\n".join(new_file_content.split("\n")[start_line : end_line + 1])

        # 准备成功消息
        success_msg = f"文件 {path} 已编辑。 "
        success_msg += self._make_output(
            snippet, f"文件 {path} 的片段", start_line + 1
        )
        success_msg += "请检查更改，确保它们符合预期。如有必要，请再次编辑文件。"

        return CLIResult(output=success_msg)

    async def insert(
        self,
        path: PathLike,
        insert_line: int,
        new_str: str,
        operator: FileOperator = None,
    ) -> CLIResult:
        """
        在文件的指定行插入文本。

        参数:
            path: 文件路径。
            insert_line: 插入的行号。
            new_str: 插入的文本内容。
            operator: 文件操作器，可选。

        返回值:
            CLIResult: 操作结果。
        """
        # 读取并准备文件内容
        file_text = (await operator.read_file(path)).expandtabs()
        new_str = new_str.expandtabs()
        file_text_lines = file_text.split("\n")
        n_lines_file = len(file_text_lines)

        # 验证 insert_line 是否有效
        if insert_line < 0 or insert_line > n_lines_file:
            raise ToolError(
                f"无效的 `insert_line` 参数：{insert_line}。它应该在文件的行范围内：[0, n_lines_file]"
            )

        # 执行插入操作
        new_str_lines = new_str.split("\n")
        new_file_text_lines = (
            file_text_lines[:insert_line]
            + new_str_lines
            + file_text_lines[insert_line:]
        )

        # 生成预览片段
        snippet_lines = (
            file_text_lines[max(0, insert_line - SNIPPET_LINES) : insert_line]
            + new_str_lines
            + file_text_lines[insert_line : insert_line + SNIPPET_LINES]
        )

        # 合并行并写入文件
        new_file_text = "\n".join(new_file_text_lines)
        snippet = "\n".join(snippet_lines)

        await operator.write_file(path, new_file_text)
        self._file_history[path].append(file_text)

        # 准备成功消息
        success_msg = f"文件 {path} 已编辑。 "
        success_msg += self._make_output(
            snippet,
            "编辑文件的片段",
            max(1, insert_line - SNIPPET_LINES + 1),
        )
        success_msg += "请检查更改，确保它们符合预期（正确的缩进，没有重复行等）。如有必要，请再次编辑文件。"

        return CLIResult(output=success_msg)

    async def undo_edit(
        self, path: PathLike, operator: FileOperator = None
    ) -> CLIResult:
        """
        撤销对文件的最后一次编辑。

        参数:
            path: 文件路径。
            operator: 文件操作器，可选。

        返回值:
            CLIResult: 操作结果。
        """
        if not self._file_history[path]:
            raise ToolError(f"未找到 {path} 的编辑历史。")

        old_text = self._file_history[path].pop()
        await operator.write_file(path, old_text)

        return CLIResult(
            output=f"成功撤销 {path} 的最后一次编辑。{self._make_output(old_text, str(path))}"
        )

    def _make_output(
        self,
        file_content: str,
        file_descriptor: str,
        init_line: int = 1,
        expand_tabs: bool = True,
    ) -> str:
        """
        格式化文件内容用于显示，带行号。

        参数:
            file_content: 文件内容。
            file_descriptor: 文件描述。
            init_line: 起始行号，默认为1。
            expand_tabs: 是否扩展制表符，默认为True。

        返回值:
            str: 格式化后的字符串。
        """
        file_content = maybe_truncate(file_content)
        if expand_tabs:
            file_content = file_content.expandtabs()

        # 为每一行添加行号
        file_content = "\n".join(
            [
                f"{i + init_line:6}\t{line}"
                for i, line in enumerate(file_content.split("\n"))
            ]
        )

        return (
            f"这是运行 `cat -n` 在 {file_descriptor} 上的结果：\n"
            + file_content
            + "\n"
        )
