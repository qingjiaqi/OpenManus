# 导入必要的类型和模块
from typing import Dict, List, Literal, Optional

from app.exceptions import ToolError
from app.tool.base import BaseTool, ToolResult


# 规划工具的描述文本
_PLANNING_TOOL_DESCRIPTION = """
一个规划工具，允许代理创建和管理用于解决复杂任务的计划。
该工具提供创建计划、更新计划步骤和跟踪进度等功能。
"""


# 规划工具类，继承自BaseTool
class PlanningTool(BaseTool):
    """
    规划工具类，用于创建和管理复杂任务的计划。
    功能包括：创建计划、更新计划步骤、跟踪进度等。
    """

    # 工具名称
    name: str = "planning"
    # 工具描述
    description: str = _PLANNING_TOOL_DESCRIPTION
    # 工具参数定义
    parameters: dict = {
        "type": "object",
        "properties": {
            "command": {
                "description": "要执行的命令。可选命令：create, update, list, get, set_active, mark_step, delete。",
                "enum": [
                    "create",
                    "update",
                    "list",
                    "get",
                    "set_active",
                    "mark_step",
                    "delete",
                ],
                "type": "string",
            },
            "plan_id": {
                "description": "计划的唯一标识符。create、update、set_active和delete命令必需，get和mark_step命令可选（未指定时使用当前活动计划）。",
                "type": "string",
            },
            "title": {
                "description": "计划的标题。create命令必需，update命令可选。",
                "type": "string",
            },
            "steps": {
                "description": "计划步骤列表。create命令必需，update命令可选。",
                "type": "array",
                "items": {"type": "string"},
            },
            "step_index": {
                "description": "要更新的步骤索引（从0开始）。mark_step命令必需。",
                "type": "integer",
            },
            "step_status": {
                "description": "步骤的状态。与mark_step命令一起使用。",
                "enum": ["not_started", "in_progress", "completed", "blocked"],
                "type": "string",
            },
            "step_notes": {
                "description": "步骤的附加注释。mark_step命令可选。",
                "type": "string",
            },
        },
        "required": ["command"],
        "additionalProperties": False,
    }

    # 存储所有计划的字典，键为plan_id，值为计划详情
    plans: dict = {}
    # 当前活动计划的ID，未设置时为None
    _current_plan_id: Optional[str] = None

    async def execute(
        self,
        *,
        command: Literal[
            "create", "update", "list", "get", "set_active", "mark_step", "delete"
        ],
        plan_id: Optional[str] = None,
        title: Optional[str] = None,
        steps: Optional[List[str]] = None,
        step_index: Optional[int] = None,
        step_status: Optional[
            Literal["not_started", "in_progress", "completed", "blocked"]
        ] = None,
        step_notes: Optional[str] = None,
        **kwargs,
    ):
        """
        执行规划工具的命令。

        参数说明：
        - command: 要执行的操作（create/update/list/get/set_active/mark_step/delete）。
        - plan_id: 计划的唯一标识符。
        - title: 计划的标题（create命令必需）。
        - steps: 计划步骤列表（create命令必需）。
        - step_index: 要更新的步骤索引（mark_step命令必需）。
        - step_status: 步骤的状态（mark_step命令使用）。
        - step_notes: 步骤的附加注释（mark_step命令可选）。
        """

        # 根据命令执行相应的操作
        if command == "create":
            # 创建新计划
            return self._create_plan(plan_id, title, steps)
        elif command == "update":
            # 更新现有计划
            return self._update_plan(plan_id, title, steps)
        elif command == "list":
            # 列出所有计划
            return self._list_plans()
        elif command == "get":
            # 获取指定计划的详情
            return self._get_plan(plan_id)
        elif command == "set_active":
            # 设置当前活动计划
            return self._set_active_plan(plan_id)
        elif command == "mark_step":
            # 标记步骤状态
            return self._mark_step(plan_id, step_index, step_status, step_notes)
        elif command == "delete":
            # 删除计划
            return self._delete_plan(plan_id)
        else:
            raise ToolError(
                f"Unrecognized command: {command}. Allowed commands are: create, update, list, get, set_active, mark_step, delete"
            )

    def _create_plan(
        self, plan_id: Optional[str], title: Optional[str], steps: Optional[List[str]]
    ) -> ToolResult:
        """
        创建新计划。

        参数：
        - plan_id: 计划的唯一标识符（必需）。
        - title: 计划的标题（必需）。
        - steps: 计划步骤列表（必需）。

        返回值：
        - ToolResult: 包含创建结果的工具返回对象。
        """
        if not plan_id:
            raise ToolError("参数 `plan_id` 对于命令: create 是必需的")

        if plan_id in self.plans:
            raise ToolError(
                f"ID 为 '{plan_id}' 的计划已存在。请使用 'update' 修改现有计划。"
            )

        if not title:
            raise ToolError("参数 `title` 对于命令: create 是必需的")

        if (
            not steps
            or not isinstance(steps, list)
            or not all(isinstance(step, str) for step in steps)
        ):
            raise ToolError(
                "参数 `steps` 必须是字符串列表，且不能为空，对于命令: create"
            )

        # 创建一个新计划，初始化步骤状态
        plan = {
            "plan_id": plan_id,
            "title": title,
            "steps": steps,
            "step_statuses": ["not_started"] * len(steps),
            "step_notes": [""] * len(steps),
        }

        self.plans[plan_id] = plan
        self._current_plan_id = plan_id  # 设置为活动计划

        return ToolResult(
            output=f"计划成功创建，ID: {plan_id}\n\n{self._format_plan(plan)}"
        )

    # 更新现有计划的方法
    def _update_plan(
        self, plan_id: Optional[str], title: Optional[str], steps: Optional[List[str]]
    ) -> ToolResult:
        """
        更新现有计划。

        参数：
        - plan_id: 计划的唯一标识符（必需）。
        - title: 计划的新标题（可选）。
        - steps: 计划的新步骤列表（可选）。

        返回值：
        - ToolResult: 包含更新结果的工具返回对象。
        """
        if not plan_id:
            raise ToolError("参数 `plan_id` 对于命令: update 是必需的")

        if plan_id not in self.plans:
            raise ToolError(f"未找到ID为: {plan_id} 的计划")

        plan = self.plans[plan_id]

        if title:
            plan["title"] = title

        if steps:
            if not isinstance(steps, list) or not all(
                isinstance(step, str) for step in steps
            ):
                raise ToolError(
                    "参数 `steps` 必须是字符串列表，对于命令: update"
                )

            # 保留未更改步骤的现有状态和说明
            old_steps = plan["steps"]
            old_statuses = plan["step_statuses"]
            old_notes = plan["step_notes"]

            # 创建新的步骤状态和说明
            new_statuses = []
            new_notes = []

            for i, step in enumerate(steps):
                # 如果步骤在相同位置存在，保留状态和说明
                if i < len(old_steps) and step == old_steps[i]:
                    new_statuses.append(old_statuses[i])
                    new_notes.append(old_notes[i])
                else:
                    new_statuses.append("not_started")
                    new_notes.append("")

            plan["steps"] = steps
            plan["step_statuses"] = new_statuses
            plan["step_notes"] = new_notes

        return ToolResult(
            output=f"计划成功更新: {plan_id}\n\n{self._format_plan(plan)}"
        )

    # 列出所有可用计划的方法
    def _list_plans(self) -> ToolResult:
        """
        列出所有可用计划。

        返回值：
        - ToolResult: 包含计划列表的工具返回对象。
        """
        if not self.plans:
            return ToolResult(
                output="没有可用的计划。请使用 'create' 命令创建一个计划。"
            )

        output = "可用计划:\n"
        for plan_id, plan in self.plans.items():
            current_marker = " (活动)" if plan_id == self._current_plan_id else ""
            completed = sum(
                1 for status in plan["step_statuses"] if status == "completed"
            )
            total = len(plan["steps"])
            progress = f"{completed}/{total} 步骤已完成"
            output += f"• {plan_id}{current_marker}: {plan['title']} - {progress}\n"

        return ToolResult(output=output)

    # 获取特定计划详细信息的方法
    def _get_plan(self, plan_id: Optional[str]) -> ToolResult:
        """
        获取指定计划的详情。

        参数：
        - plan_id: 计划的唯一标识符（可选，未指定时使用当前活动计划）。

        返回值：
        - ToolResult: 包含计划详情的工具返回对象。
        """
        if not plan_id:
            # 如果未提供 plan_id，使用当前活动计划
            if not self._current_plan_id:
                raise ToolError(
                    "没有活动计划。请指定一个 plan_id 或设置一个活动计划。"
                )
            plan_id = self._current_plan_id

        if plan_id not in self.plans:
            raise ToolError(f"未找到ID为: {plan_id} 的计划")

        plan = self.plans[plan_id]
        return ToolResult(output=self._format_plan(plan))

    # 设置活动计划的方法
    def _set_active_plan(self, plan_id: Optional[str]) -> ToolResult:
        """
        设置当前活动计划。

        参数：
        - plan_id: 计划的唯一标识符（必需）。

        返回值：
        - ToolResult: 包含设置结果的工具返回对象。
        """
        if not plan_id:
            raise ToolError("参数 `plan_id` 对于命令: set_active 是必需的")

        if plan_id not in self.plans:
            raise ToolError(f"未找到ID为: {plan_id} 的计划")

        self._current_plan_id = plan_id
        return ToolResult(
            output=f"计划 '{plan_id}' 现在是活动计划。\n\n{self._format_plan(self.plans[plan_id])}"
        )

    # 标记步骤状态和说明的方法
    def _mark_step(
        self,
        plan_id: Optional[str],
        step_index: Optional[int],
        step_status: Optional[str],
        step_notes: Optional[str],
    ) -> ToolResult:
        """
        标记步骤状态和添加注释。

        参数：
        - plan_id: 计划的唯一标识符（可选，未指定时使用当前活动计划）。
        - step_index: 要更新的步骤索引（必需）。
        - step_status: 步骤的新状态（可选）。
        - step_notes: 步骤的附加注释（可选）。

        返回值：
        - ToolResult: 包含更新结果的工具返回对象。
        """
        if not plan_id:
            # 如果未提供 plan_id，使用当前活动计划
            if not self._current_plan_id:
                raise ToolError(
                    "没有活动计划。请指定一个 plan_id 或设置一个活动计划。"
                )
            plan_id = self._current_plan_id

        if plan_id not in self.plans:
            raise ToolError(f"未找到ID为: {plan_id} 的计划")

        if step_index is None:
            raise ToolError("参数 `step_index` 对于命令: mark_step 是必需的")

        plan = self.plans[plan_id]

        if step_index < 0 or step_index >= len(plan["steps"]):
            raise ToolError(
                f"无效的 step_index: {step_index}。有效索引范围为 0 到 {len(plan['steps'])-1}。"
            )

        if step_status and step_status not in [
            "not_started",
            "in_progress",
            "completed",
            "blocked",
        ]:
            raise ToolError(
                f"无效的 step_status: {step_status}。有效状态为：not_started, in_progress, completed, blocked"
            )

        if step_status:
            plan["step_statuses"][step_index] = step_status

        if step_notes:
            plan["step_notes"][step_index] = step_notes

        return ToolResult(
            output=f"计划 '{plan_id}' 中的步骤 {step_index} 已更新。\n\n{self._format_plan(plan)}"
        )

    # 删除计划的方法
    def _delete_plan(self, plan_id: Optional[str]) -> ToolResult:
        """
        删除指定计划。

        参数：
        - plan_id: 计划的唯一标识符（必需）。

        返回值：
        - ToolResult: 包含删除结果的工具返回对象。
        """
        if not plan_id:
            raise ToolError("参数 `plan_id` 对于命令: delete 是必需的")

        if plan_id not in self.plans:
            raise ToolError(f"未找到ID为: {plan_id} 的计划")

        del self.plans[plan_id]

        # 如果删除的计划是活动计划，清除活动计划
        if self._current_plan_id == plan_id:
            self._current_plan_id = None

        return ToolResult(output=f"计划 '{plan_id}' 已被删除。")

    # 格式化计划以显示的方法
    def _format_plan(self, plan: Dict) -> str:
        """
        格式化计划详情以便显示。

        参数：
        - plan: 包含计划详情的字典。

        返回值：
        - str: 格式化后的计划详情字符串。
        """
        output = f"计划: {plan['title']} (ID: {plan['plan_id']})\n"
        output += "=" * len(output) + "\n\n"

        # 计算进度统计信息
        total_steps = len(plan["steps"])
        completed = sum(1 for status in plan["step_statuses"] if status == "completed")
        in_progress = sum(
            1 for status in plan["step_statuses"] if status == "in_progress"
        )
        blocked = sum(1 for status in plan["step_statuses"] if status == "blocked")
        not_started = sum(
            1 for status in plan["step_statuses"] if status == "not_started"
        )

        output += f"进度: {completed}/{total_steps} 步骤已完成 "
        if total_steps > 0:
            percentage = (completed / total_steps) * 100
            output += f"({percentage:.1f}%)\n"
        else:
            output += "(0%)\n"

        output += f"状态: {completed} 已完成, {in_progress} 进行中, {blocked} 阻塞, {not_started} 未开始\n\n"
        output += "步骤:\n"

        # 添加每个步骤及其状态和说明
        for i, (step, status, notes) in enumerate(
            zip(plan["steps"], plan["step_statuses"], plan["step_notes"])
        ):
            status_symbol = {
                "not_started": "[ ]",
                "in_progress": "[→]",
                "completed": "[✓]",
                "blocked": "[!]",
            }.get(status, "[ ]")

            output += f"{i}. {status_symbol} {step}\n"
            if notes:
                output += f"   说明: {notes}\n"

        return output
