# 导入异步IO库
import asyncio
# 导入时间库，用于计算执行时间
import time

# 从本地模块导入Manus类、FlowFactory类和FlowType枚举
from app.agent.manus import Manus
from app.flow.flow_factory import FlowFactory, FlowType
# 从本地模块导入logger对象
from app.logger import logger


# 定义异步函数run_flow，用于运行流程
# 用途：根据用户输入的提示词执行流程，并记录执行时间和结果
# 返回值：无
async def run_flow():
    # 初始化代理字典，包含Manus代理实例
    agents = {
        "manus": Manus(),
    }

    try:
        # 获取用户输入的提示词
        prompt = input("Enter your prompt: ")

        # 检查提示词是否为空或仅包含空白字符
        if prompt.strip().isspace() or not prompt:
            logger.warning("Empty prompt provided.")
            return

        # 创建流程实例，类型为PLANNING
        flow = FlowFactory.create_flow(
            flow_type=FlowType.PLANNING,
            agents=agents,
        )
        # 记录日志：开始处理请求
        logger.warning("Processing your request...")

        try:
            # 记录开始时间
            start_time = time.time()
            # 异步执行流程，设置超时时间为1小时
            result = await asyncio.wait_for(
                flow.execute(prompt),
                timeout=3600,  # 60分钟超时
            )
            # 计算执行时间
            elapsed_time = time.time() - start_time
            # 记录日志：请求处理完成及耗时
            logger.info(f"Request processed in {elapsed_time:.2f} seconds")
            logger.info(result)
        except asyncio.TimeoutError:
            # 捕获超时异常，记录日志
            logger.error("Request processing timed out after 1 hour")
            logger.info(
                "Operation terminated due to timeout. Please try a simpler request."
            )

    except KeyboardInterrupt:
        # 捕获键盘中断异常，记录日志
        logger.info("Operation cancelled by user.")
    except Exception as e:
        # 捕获其他异常，记录日志
        logger.error(f"Error: {str(e)}")


# 程序入口点
if __name__ == "__main__":
    # 运行run_flow函数
    asyncio.run(run_flow())
