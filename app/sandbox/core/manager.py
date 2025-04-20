import asyncio  # 异步IO库，用于处理异步任务
import uuid  # 生成唯一标识符
from contextlib import asynccontextmanager  # 异步上下文管理器
from typing import Dict, Optional, Set  # 类型注解

import docker  # Docker SDK，用于与Docker交互
from docker.errors import APIError, ImageNotFound  # Docker API错误和镜像未找到异常

from app.config import SandboxSettings  # 沙箱配置类
from app.logger import logger  # 日志记录器
from app.sandbox.core.sandbox import DockerSandbox  # Docker沙箱实现类


class SandboxManager:
    """Docker沙箱管理器。

    管理多个DockerSandbox实例的生命周期，包括创建、监控和清理。
    提供并发访问控制和自动清理机制。

    属性:
        max_sandboxes: 最大允许的沙箱数量。
        idle_timeout: 沙箱空闲超时时间（秒）。
        cleanup_interval: 清理检查间隔（秒）。
        _sandboxes: 活跃沙箱实例的映射。
        _last_used: 沙箱最后使用时间记录。
    """

    def __init__(
        self,
        max_sandboxes: int = 100,
        idle_timeout: int = 3600,
        cleanup_interval: int = 300,
    ):
        """初始化沙箱管理器。

        参数:
            max_sandboxes: 最大沙箱数量限制。
            idle_timeout: 空闲超时时间（秒）。
            cleanup_interval: 清理检查间隔（秒）。
        """
        self.max_sandboxes = max_sandboxes  # 最大沙箱数量
        self.idle_timeout = idle_timeout  # 空闲超时时间
        self.cleanup_interval = cleanup_interval  # 清理检查间隔

        # Docker客户端
        self._client = docker.from_env()

        # 资源映射
        self._sandboxes: Dict[str, DockerSandbox] = {}  # 沙箱ID到实例的映射
        self._last_used: Dict[str, float] = {}  # 沙箱最后使用时间

        # 并发控制
        self._locks: Dict[str, asyncio.Lock] = {}  # 沙箱操作锁
        self._global_lock = asyncio.Lock()  # 全局锁
        self._active_operations: Set[str] = set()  # 活跃操作集合

        # 清理任务
        self._cleanup_task: Optional[asyncio.Task] = None  # 清理任务
        self._is_shutting_down = False  # 是否正在关闭

        # 启动自动清理任务
        self.start_cleanup_task()

    async def ensure_image(self, image: str) -> bool:
        """确保Docker镜像可用。

        参数:
            image: 镜像名称。

        返回:
            bool: 镜像是否可用。
        """
        try:
            self._client.images.get(image)  # 检查镜像是否存在
            return True
        except ImageNotFound:
            try:
                logger.info(f"Pulling image {image}...")  # 日志记录拉取镜像
                await asyncio.get_event_loop().run_in_executor(
                    None, self._client.images.pull, image  # 异步拉取镜像
                )
                return True
            except (APIError, Exception) as e:
                logger.error(f"Failed to pull image {image}: {e}")  # 拉取失败日志
                return False

    @asynccontextmanager
    async def sandbox_operation(self, sandbox_id: str):
        """沙箱操作上下文管理器。

        提供并发控制和最后使用时间更新。

        参数:
            sandbox_id: 沙箱ID。

        抛出:
            KeyError: 如果沙箱不存在。
        """
        if sandbox_id not in self._locks:
            self._locks[sandbox_id] = asyncio.Lock()  # 为沙箱创建锁

        async with self._locks[sandbox_id]:  # 加锁
            if sandbox_id not in self._sandboxes:
                raise KeyError(f"Sandbox {sandbox_id} not found")  # 沙箱不存在时抛出异常

            self._active_operations.add(sandbox_id)  # 标记为活跃操作
            try:
                self._last_used[sandbox_id] = asyncio.get_event_loop().time()  # 更新最后使用时间
                yield self._sandboxes[sandbox_id]  # 返回沙箱实例
            finally:
                self._active_operations.remove(sandbox_id)  # 移除活跃标记

    async def create_sandbox(
        self,
        config: Optional[SandboxSettings] = None,
        volume_bindings: Optional[Dict[str, str]] = None,
    ) -> str:
        """创建新的沙箱实例。

        参数:
            config: 沙箱配置。
            volume_bindings: 卷绑定配置。

        返回:
            str: 沙箱ID。

        抛出:
            RuntimeError: 如果达到最大沙箱数量或创建失败。
        """
        async with self._global_lock:  # 全局加锁
            if len(self._sandboxes) >= self.max_sandboxes:  # 检查沙箱数量限制
                raise RuntimeError(
                    f"Maximum number of sandboxes ({self.max_sandboxes}) reached"
                )

            config = config or SandboxSettings()  # 使用默认配置
            if not await self.ensure_image(config.image):  # 确保镜像可用
                raise RuntimeError(f"Failed to ensure Docker image: {config.image}")

            sandbox_id = str(uuid.uuid4())  # 生成唯一ID
            try:
                sandbox = DockerSandbox(config, volume_bindings)  # 创建沙箱实例
                await sandbox.create()  # 初始化沙箱

                self._sandboxes[sandbox_id] = sandbox  # 记录沙箱
                self._last_used[sandbox_id] = asyncio.get_event_loop().time()  # 更新最后使用时间
                self._locks[sandbox_id] = asyncio.Lock()  # 为沙箱创建锁

                logger.info(f"Created sandbox {sandbox_id}")  # 日志记录
                return sandbox_id

            except Exception as e:
                logger.error(f"Failed to create sandbox: {e}")  # 错误日志
                if sandbox_id in self._sandboxes:
                    await self.delete_sandbox(sandbox_id)  # 清理失败的沙箱
                raise RuntimeError(f"Failed to create sandbox: {e}")

    async def get_sandbox(self, sandbox_id: str) -> DockerSandbox:
        """获取沙箱实例。

        参数:
            sandbox_id: 沙箱ID。

        返回:
            DockerSandbox: 沙箱实例。

        抛出:
            KeyError: 如果沙箱不存在。
        """
        async with self.sandbox_operation(sandbox_id) as sandbox:  # 使用上下文管理器
            return sandbox  # 返回沙箱实例

    def start_cleanup_task(self) -> None:
        """启动自动清理任务。"""
        async def cleanup_loop():
            while not self._is_shutting_down:  # 循环直到关闭
                try:
                    await self._cleanup_idle_sandboxes()  # 清理空闲沙箱
                except Exception as e:
                    logger.error(f"Error in cleanup loop: {e}")  # 错误日志
                await asyncio.sleep(self.cleanup_interval)  # 等待下次清理

        self._cleanup_task = asyncio.create_task(cleanup_loop())  # 创建异步任务

    async def _cleanup_idle_sandboxes(self) -> None:
        """清理空闲沙箱。"""
        current_time = asyncio.get_event_loop().time()  # 获取当前时间
        to_cleanup = []  # 待清理的沙箱列表

        async with self._global_lock:  # 全局加锁
            for sandbox_id, last_used in self._last_used.items():  # 遍历沙箱使用记录
                if (
                    sandbox_id not in self._active_operations  # 沙箱无活跃操作
                    and current_time - last_used > self.idle_timeout  # 超过空闲超时时间
                ):
                    to_cleanup.append(sandbox_id)  # 添加到待清理列表

        for sandbox_id in to_cleanup:  # 清理空闲沙箱
            try:
                await self.delete_sandbox(sandbox_id)  # 删除沙箱
            except Exception as e:
                logger.error(f"Error cleaning up sandbox {sandbox_id}: {e}")  # 错误日志

    async def cleanup(self) -> None:
        """清理所有资源。"""
        logger.info("Starting manager cleanup...")  # 日志记录
        self._is_shutting_down = True  # 标记为关闭状态

        # 取消清理任务
        if self._cleanup_task:
            self._cleanup_task.cancel()  # 取消任务
            try:
                await asyncio.wait_for(self._cleanup_task, timeout=1.0)  # 等待任务结束
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass  # 忽略取消或超时异常

        # 获取所有待清理的沙箱ID
        async with self._global_lock:
            sandbox_ids = list(self._sandboxes.keys())

        # 并发清理所有沙箱
        cleanup_tasks = []
        for sandbox_id in sandbox_ids:
            task = asyncio.create_task(self._safe_delete_sandbox(sandbox_id))  # 创建清理任务
            cleanup_tasks.append(task)

        if cleanup_tasks:
            # 等待所有清理任务完成，设置超时避免无限等待
            try:
                await asyncio.wait(cleanup_tasks, timeout=30.0)
            except asyncio.TimeoutError:
                logger.error("Sandbox cleanup timed out")  # 超时日志

        # 清理剩余引用
        self._sandboxes.clear()
        self._last_used.clear()
        self._locks.clear()
        self._active_operations.clear()

        logger.info("Manager cleanup completed")  # 日志记录

    async def _safe_delete_sandbox(self, sandbox_id: str) -> None:
        """安全删除单个沙箱。

        参数:
            sandbox_id: 待删除的沙箱ID。
        """
        try:
            if sandbox_id in self._active_operations:  # 检查沙箱是否有活跃操作
                logger.warning(
                    f"Sandbox {sandbox_id} has active operations, waiting for completion"
                )
                for _ in range(10):  # 最多等待10次
                    await asyncio.sleep(0.5)  # 每次等待0.5秒
                    if sandbox_id not in self._active_operations:  # 检查操作是否完成
                        break
                else:
                    logger.warning(
                        f"Timeout waiting for sandbox {sandbox_id} operations to complete"
                    )

            # 获取沙箱对象引用
            sandbox = self._sandboxes.get(sandbox_id)
            if sandbox:
                await sandbox.cleanup()  # 清理沙箱资源

                # 从管理器中移除沙箱记录
                async with self._global_lock:
                    self._sandboxes.pop(sandbox_id, None)  # 移除沙箱
                    self._last_used.pop(sandbox_id, None)  # 移除最后使用时间
                    self._locks.pop(sandbox_id, None)  # 移除锁
                    logger.info(f"Deleted sandbox {sandbox_id}")  # 日志记录
        except Exception as e:
            logger.error(f"Error during cleanup of sandbox {sandbox_id}: {e}")  # 错误日志

    async def delete_sandbox(self, sandbox_id: str) -> None:
        """删除指定沙箱。

        参数:
            sandbox_id: 沙箱ID。
        """
        if sandbox_id not in self._sandboxes:  # 检查沙箱是否存在
            return

        try:
            await self._safe_delete_sandbox(sandbox_id)  # 调用安全删除方法
        except Exception as e:
            logger.error(f"Failed to delete sandbox {sandbox_id}: {e}")  # 错误日志

    async def __aenter__(self) -> "SandboxManager":
        """异步上下文管理器入口。"""
        return self  # 返回管理器实例

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """异步上下文管理器退出。"""
        await self.cleanup()  # 清理资源

    def get_stats(self) -> Dict:
        """获取管理器统计信息。

        返回:
            Dict: 统计信息。
        """
        return {
            "total_sandboxes": len(self._sandboxes),  # 总沙箱数量
            "active_operations": len(self._active_operations),  # 活跃操作数量
            "max_sandboxes": self.max_sandboxes,  # 最大沙箱数量
            "idle_timeout": self.idle_timeout,  # 空闲超时时间
            "cleanup_interval": self.cleanup_interval,  # 清理间隔
            "is_shutting_down": self._is_shutting_down,  # 是否正在关闭
        }
