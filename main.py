# 导入异步IO库
import asyncio

# 从本地模块导入Manus类和logger对象
from app.agent.manus import Manus
from app.logger import logger


# 定义主函数，用于运行代理程序
async def main():
    # 初始化Manus代理实例
    agent = Manus()
    try:
        # 获取用户输入的提示词
        prompt = input("Enter your prompt: ")
        # 检查提示词是否为空
        if not prompt.strip():
            logger.warning("Empty prompt provided.")
            return

        # 记录日志：开始处理请求
        logger.warning("Processing your request...")
        # 异步运行代理处理提示词
        await agent.run(prompt)
        # 记录日志：请求处理完成
        logger.info("Request processing completed.")
    except KeyboardInterrupt:
        # 捕获键盘中断异常，记录日志
        logger.warning("Operation interrupted.")
    finally:
        # 确保在退出前清理代理资源
        await agent.cleanup()


# 程序入口点
if __name__ == "__main__":
    # 运行主函数
    asyncio.run(main())
