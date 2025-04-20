# 导入必要的类型和模块
from typing import List, Optional

from pydantic import BaseModel, Field


# 表示单个搜索结果的模型类
class SearchItem(BaseModel):
    """表示单个搜索结果项"""

    # 搜索结果的标题
    title: str = Field(description="搜索结果的标题")
    # 搜索结果的URL
    url: str = Field(description="搜索结果的URL")
    # 搜索结果的描述（可选）
    description: Optional[str] = Field(
        default=None, description="搜索结果的描述或摘要"
    )

    # 返回搜索结果的字符串表示形式
    def __str__(self) -> str:
        """返回搜索结果的字符串表示形式"""
        return f"{self.title} - {self.url}"


# 搜索引擎的抽象基类
class WebSearchEngine(BaseModel):
    """搜索引擎的基类，定义了搜索接口"""

    # 允许任意类型配置
    model_config = {"arbitrary_types_allowed": True}

    # 执行搜索并返回搜索结果列表
    def perform_search(
        self, query: str, num_results: int = 10, *args, **kwargs
    ) -> List[SearchItem]:
        """
        执行搜索并返回搜索结果列表

        参数:
            query (str): 搜索关键词
            num_results (int, optional): 返回的搜索结果数量，默认为10
            args: 额外参数
            kwargs: 额外关键字参数

        返回:
            List[SearchItem]: 匹配搜索关键词的搜索结果列表
        """
        raise NotImplementedError
