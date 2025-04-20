import sys
from datetime import datetime

from loguru import logger as _logger

from app.config import PROJECT_ROOT


# 全局变量，定义默认的日志打印级别
_print_level = "INFO"


def define_log_level(print_level="INFO", logfile_level="DEBUG", name: str = None):
    """
    调整日志级别并配置日志输出

    参数:
        print_level (str): 控制台日志级别，默认为 "INFO"
        logfile_level (str): 文件日志级别，默认为 "DEBUG"
        name (str): 日志文件名的前缀，可选

    返回值:
        logger: 配置好的日志记录器
    """
    global _print_level
    _print_level = print_level  # 更新全局日志级别

    # 获取当前时间并格式化为字符串，用于日志文件名
    current_date = datetime.now()
    formatted_date = current_date.strftime("%Y%m%d%H%M%S")
    # 如果提供了 name 参数，则日志文件名包含前缀
    log_name = (
        f"{name}_{formatted_date}" if name else formatted_date
    )

    # 移除默认的日志处理器
    _logger.remove()
    # 添加控制台日志处理器
    _logger.add(sys.stderr, level=print_level)
    # 添加文件日志处理器，日志文件保存在项目根目录的 logs 文件夹下
    _logger.add(PROJECT_ROOT / f"logs/{log_name}.log", level=logfile_level)
    return _logger


# 初始化日志记录器
logger = define_log_level()


if __name__ == "__main__":
    # 测试日志功能
    logger.info("Starting application")
    logger.debug("Debug message")
    logger.warning("Warning message")
    logger.error("Error message")
    logger.critical("Critical message")

    try:
        raise ValueError("Test error")
    except Exception as e:
        logger.exception(f"An error occurred: {e}")
