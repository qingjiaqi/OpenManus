"""
文件操作工具，支持沙箱环境。
功能包括查看、创建、编辑文件，以及字符串替换和撤销编辑操作。
"""

# 导入必要的库和模块
from collections import defaultdict  # 用于创建默认字典
from pathlib import Path  # 用于处理文件路径
from typing import Any, DefaultDict, List, Literal, Optional, get_args  # 类型注解支持

# 导入项目自定义模块
from app.config import config  # 配置文件
from app.exceptions import ToolError  # 自定义异常类
from app.tool import BaseTool  # 工具基类
from app.tool.base import CLIResult, ToolResult  # 工具执行结果类
from app.tool.file_operators import (
    FileOperator,  # 文件操作接口
    LocalFileOperator,  # 本地文件操作实现
    PathLike,  # 路径类型别名
    SandboxFileOperator,  # 沙箱文件操作实现
)


# 定义支持的命令类型
Command = Literal[
    "view",  # 查看文件或目录
    "create",  # 创建文件
    "str_replace",  # 字符串替换
    "insert",  # 插入内容
    "undo_edit",  # 撤销编辑
]

# 常量定义
SNIPPET_LINES: int = 4  # 显示代码片段的行数
MAX_RESPONSE_LEN: int = 16000  # 最大响应长度
TRUNCATED_MESSAGE: str = (
    "<response clipped><NOTE>To save on context only part of this file has been shown to you. "
    "You should retry this tool after you have searched inside the file with `grep -n` "
    "in order to find the line numbers of what you are looking for.</NOTE>"
)

# 工具描述
_STR_REPLACE_EDITOR_DESCRIPTION = """自定义编辑工具，用于查看、创建和编辑文件
* 状态在命令调用和用户讨论之间持久化
* 如果`path`是文件，`view`命令显示`cat -n`的结果；如果是目录，`view`列出非隐藏文件和目录（最多2层深度）
* `create`命令不能用于已存在的文件路径
* 如果命令生成过长输出，将被截断并标记为`<response clipped>`
* `undo_edit`命令将撤销对文件`path`的最后一次编辑

使用`str_replace`命令的注意事项：
* `old_str`参数必须完全匹配文件中的一行或多行连续内容，注意空格！
* 如果`old_str`在文件中不唯一，替换操作不会执行。确保`old_str`包含足够的上下文以使其唯一
* `new_str`参数包含替换`old_str`的新内容
"""


class StrReplaceEditor(BaseTool):
    """
    字符串替换编辑器工具类，支持查看、创建、编辑文件，并提供沙箱环境支持。
    """

    name: str = "str_replace_editor"
    description: str = _STR_REPLACE_EDITOR_DESCRIPTION
    parameters: dict = {
        "type": "object",
        "properties": {
            "command": {
                "description": "要运行的命令。可选值为：`view`, `create`, `str_replace`, `insert`, `undo_edit`。",
                "enum": ["view", "create", "str_replace", "insert", "undo_edit"],
                "type": "string",
            },
            "path": {
                "description": "文件或目录的绝对路径。",
                "type": "string",
            },
            "file_text": {
                "description": "`create`命令的必需参数，指定要创建的文件内容。",
                "type": "string",
            },
            "old_str": {
                "description": "`str_replace`命令的必需参数，指定要替换的字符串。",
                "type": "string",
            },
            "new_str": {
                "description": "`str_replace`或`insert`命令的可选参数，指定替换或插入的新字符串。",
                "type": "string",
            },
            "insert_line": {
                "description": "`insert`命令的必需参数，指定插入新字符串的行号（从1开始）。",
                "type": "integer",
            },
            "view_range": {
                "description": "`view`命令的可选参数，指定查看文件的行号范围（例如[11, 12]显示第11和12行）。",
                "items": {"type": "integer"},
                "type": "array",
            },
        },
        "required": ["command", "path"],
    }
    _file_history: DefaultDict[PathLike, List[str]] = defaultdict(list)
    _local_operator: LocalFileOperator = LocalFileOperator()
    _sandbox_operator: SandboxFileOperator = SandboxFileOperator()

    # def _get_operator(self, use_sandbox: bool) -> FileOperator:
    def _get_operator(self) -> FileOperator:
        """
        根据执行模式获取适当的文件操作器。

        返回:
            FileOperator: 本地文件操作器或沙箱文件操作器，取决于配置是否启用沙箱模式。
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

        参数:
            command (Command): 要执行的命令（view、create、str_replace、insert、undo_edit）。
            path (str): 文件或目录的绝对路径。
            file_text (str | None): `create` 命令的文件内容。
            view_range (list[int] | None): `view` 命令的行号范围。
            old_str (str | None): `str_replace` 命令的旧字符串。
            new_str (str | None): `str_replace` 或 `insert` 命令的新字符串。
            insert_line (int | None): `insert` 命令的插入行号。
            **kwargs: 其他可选参数。

        返回:
            str: 命令执行结果的字符串表示。
        """
        # 获取适当的文件操作器
        operator = self._get_operator()

        # 验证路径和命令组合
        await self.validate_path(command, Path(path), operator)

        # 执行相应的命令
        if command == "view":
            result = await self.view(path, view_range, operator)
        elif command == "create":
            if file_text is None:
                raise ToolError("Parameter `file_text` is required for command: create")
            await operator.write_file(path, file_text)
            self._file_history[path].append(file_text)
            result = ToolResult(output=f"File created successfully at: {path}")
        elif command == "str_replace":
            if old_str is None:
                raise ToolError(
                    "Parameter `old_str` is required for command: str_replace"
                )
            result = await self.str_replace(path, old_str, new_str, operator)
        elif command == "insert":
            if insert_line is None:
                raise ToolError(
                    "Parameter `insert_line` is required for command: insert"
                )
            if new_str is None:
                raise ToolError("Parameter `new_str` is required for command: insert")
            result = await self.insert(path, insert_line, new_str, operator)
        elif command == "undo_edit":
            result = await self.undo_edit(path, operator)
        else:
            # 此情况应由类型检查捕获，但为了安全起见包含
            raise ToolError(
                f'Unrecognized command {command}. The allowed commands for the {self.name} tool are: {", ".join(get_args(Command))}'
            )

        return str(result)

    async def validate_path(
        self, command: str, path: Path, operator: FileOperator
    ) -> None:
        """
        验证路径和命令组合是否有效。

        参数:
            command (str): 要执行的命令。
            path (Path): 文件或目录的路径。
            operator (FileOperator): 文件操作器。

        抛出:
            ToolError: 如果路径或命令组合无效。
        """
        # 检查路径是否为绝对路径
        if not path.is_absolute():
            raise ToolError(f"The path {path} is not an absolute path")

        # 仅对非 create 命令检查路径是否存在
        if command != "create":
            if not await operator.exists(path):
                raise ToolError(
                    f"The path {path} does not exist. Please provide a valid path."
                )

            # 检查路径是否为目录
            is_dir = await operator.is_directory(path)
            if is_dir and command != "view":
                raise ToolError(
                    f"The path {path} is a directory and only the `view` command can be used on directories"
                )

        # 检查 create 命令的文件是否已存在
        elif command == "create":
            exists = await operator.exists(path)
            if exists:
                raise ToolError(
                    f"File already exists at: {path}. Cannot overwrite files using command `create`."
                )

    async def view(
        self,
        path: PathLike,
        view_range: Optional[List[int]] = None,
        operator: FileOperator = None,
    ) -> CLIResult:
        """
        查看文件或目录内容。

        参数:
            path (PathLike): 文件或目录的路径。
            view_range (Optional[List[int]]): 查看文件的行号范围。
            operator (FileOperator): 文件操作器。

        返回:
            CLIResult: 包含查看结果的命令行结果对象。
        """
        # 确定路径是否为目录
        is_dir = await operator.is_directory(path)

        if is_dir:
            # 目录处理
            if view_range:
                raise ToolError(
                    "The `view_range` parameter is not allowed when `path` points to a directory."
                )

            return await self._view_directory(path, operator)
        else:
            # 文件处理
            return await self._view_file(path, operator, view_range)

    @staticmethod
    async def _view_directory(path: PathLike, operator: FileOperator) -> CLIResult:
        """
        查看目录内容。

        参数:
            path (PathLike): 目录路径。
            operator (FileOperator): 文件操作器。

        返回:
            CLIResult: 包含目录内容的命令行结果对象。
        """
        find_cmd = f"find {path} -maxdepth 2 -not -path '*/\.*'"

        # 使用操作器执行命令
        returncode, stdout, stderr = await operator.run_command(find_cmd)

        if not stderr:
            stdout = (
                f"Here's the files and directories up to 2 levels deep in {path}, "
                f"excluding hidden items:\n{stdout}\n"
            )

        return CLIResult(output=stdout, error=stderr)

    async def _view_file(
        self,
        path: PathLike,
        operator: FileOperator,
        view_range: Optional[List[int]] = None,
    ) -> CLIResult:
        """
        查看文件内容，可选行号范围。

        参数:
            path (PathLike): 文件路径。
            operator (FileOperator): 文件操作器。
            view_range (Optional[List[int]]): 查看的行号范围。

        返回:
            CLIResult: 包含文件内容的命令行结果对象。
        """
        # 读取文件内容
        file_content = await operator.read_file(path)
        init_line = 1

        # 如果指定了行号范围，则应用范围
        if view_range:
            if len(view_range) != 2 or not all(isinstance(i, int) for i in view_range):
                raise ToolError(
                    "Invalid `view_range`. It should be a list of two integers."
                )

            file_lines = file_content.split("\n")
            n_lines_file = len(file_lines)
            init_line, final_line = view_range

            # 验证行号范围
            if init_line < 1 or init_line > n_lines_file:
                raise ToolError(
                    f"Invalid `view_range`: {view_range}. Its first element `{init_line}` should be "
                    f"within the range of lines of the file: {[1, n_lines_file]}"
                )
            if final_line > n_lines_file:
                raise ToolError(
                    f"Invalid `view_range`: {view_range}. Its second element `{final_line}` should be "
                    f"smaller than the number of lines in the file: `{n_lines_file}`"
                )
            if final_line != -1 and final_line < init_line:
                raise ToolError(
                    f"Invalid `view_range`: {view_range}. Its second element `{final_line}` should be "
                    f"larger or equal than its first `{init_line}`"
                )

            # 应用范围
            if final_line == -1:
                file_content = "\n".join(file_lines[init_line - 1 :])
            else:
                file_content = "\n".join(file_lines[init_line - 1 : final_line])

        # 格式化并返回结果
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
            path (PathLike): 文件路径。
            old_str (str): 要替换的旧字符串。
            new_str (Optional[str]): 替换的新字符串。
            operator (FileOperator): 文件操作器。

        返回:
            CLIResult: 包含替换结果的命令行结果对象。
        """
        # 读取文件内容并扩展制表符
        file_content = (await operator.read_file(path)).expandtabs()
        old_str = old_str.expandtabs()
        new_str = new_str.expandtabs() if new_str is not None else ""

        # 检查旧字符串在文件中是否唯一
        occurrences = file_content.count(old_str)
        if occurrences == 0:
            raise ToolError(
                f"No replacement was performed, old_str `{old_str}` did not appear verbatim in {path}."
            )
        elif occurrences > 1:
            # 查找旧字符串出现的行号
            file_content_lines = file_content.split("\n")
            lines = [
                idx + 1
                for idx, line in enumerate(file_content_lines)
                if old_str in line
            ]
            raise ToolError(
                f"No replacement was performed. Multiple occurrences of old_str `{old_str}` "
                f"in lines {lines}. Please ensure it is unique"
            )

        # 替换旧字符串为新字符串
        new_file_content = file_content.replace(old_str, new_str)

        # 将新内容写入文件
        await operator.write_file(path, new_file_content)

        # 将原始内容保存到历史记录
        self._file_history[path].append(file_content)

        # 创建编辑部分的代码片段
        replacement_line = file_content.split(old_str)[0].count("\n")
        start_line = max(0, replacement_line - SNIPPET_LINES)
        end_line = replacement_line + SNIPPET_LINES + new_str.count("\n")
        snippet = "\n".join(new_file_content.split("\n")[start_line : end_line + 1])

        # 准备成功消息
        success_msg = f"The file {path} has been edited. "
        success_msg += self._make_output(
            snippet, f"a snippet of {path}", start_line + 1
        )
        success_msg += "Review the changes and make sure they are as expected. Edit the file again if necessary."

        return CLIResult(output=success_msg)

    async def insert(
        self,
        path: PathLike,
        insert_line: int,
        new_str: str,
        operator: FileOperator = None,
    ) -> CLIResult:
        """
        在文件的指定行插入新内容。

        参数:
            path (PathLike): 文件路径。
            insert_line (int): 插入新内容的行号（从1开始）。
            new_str (str): 要插入的新字符串。
            operator (FileOperator): 文件操作器。

        返回:
            CLIResult: 包含插入结果的命令行结果对象。
        """
        # 读取并准备内容
        file_text = (await operator.read_file(path)).expandtabs()
        new_str = new_str.expandtabs()
        file_text_lines = file_text.split("\n")
        n_lines_file = len(file_text_lines)

        # 验证插入行号
        if insert_line < 0 or insert_line > n_lines_file:
            raise ToolError(
                f"Invalid `insert_line` parameter: {insert_line}. It should be within "
                f"the range of lines of the file: {[0, n_lines_file]}"
            )

        # 执行插入操作
        new_str_lines = new_str.split("\n")
        new_file_text_lines = (
            file_text_lines[:insert_line]
            + new_str_lines
            + file_text_lines[insert_line:]
        )

        # 创建预览片段
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
        success_msg = f"The file {path} has been edited. "
        success_msg += self._make_output(
            snippet,
            "a snippet of the edited file",
            max(1, insert_line - SNIPPET_LINES + 1),
        )
        success_msg += "Review the changes and make sure they are as expected (correct indentation, no duplicate lines, etc). Edit the file again if necessary."

        return CLIResult(output=success_msg)

    async def undo_edit(
        self, path: PathLike, operator: FileOperator = None
    ) -> CLIResult:
        """
        撤销对文件的最后一次编辑。

        参数:
            path (PathLike): 文件路径。
            operator (FileOperator): 文件操作器。

        返回:
            CLIResult: 包含撤销结果的命令行结果对象。
        """
        if not self._file_history[path]:
            raise ToolError(f"No edit history found for {path}.")

        old_text = self._file_history[path].pop()
        await operator.write_file(path, old_text)

        return CLIResult(
            output=f"Last edit to {path} undone successfully. {self._make_output(old_text, str(path))}"
        )

    def _make_output(
        self,
        file_content: str,
        file_descriptor: str,
        init_line: int = 1,
        expand_tabs: bool = True,
    ) -> str:
        """
        格式化文件内容以便显示，并添加行号。

        参数:
            file_content (str): 文件内容。
            file_descriptor (str): 文件描述符。
            init_line (int): 起始行号（默认为1）。
            expand_tabs (bool): 是否扩展制表符（默认为True）。

        返回:
            str: 格式化后的文件内容字符串。
        """
        file_content = maybe_truncate(file_content)
        if expand_tabs:
            file_content = file_content.expandtabs()

        # 为每行添加行号
        file_content = "\n".join(
            [
                f"{i + init_line:6}\t{line}"
                for i, line in enumerate(file_content.split("\n"))
            ]
        )

        return (
            f"Here's the result of running `cat -n` on {file_descriptor}:\n"
            + file_content
            + "\n"
        )
