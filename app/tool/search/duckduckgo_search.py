# 导入必要的模块
# typing.List 用于类型注解，表示返回列表类型
from typing import List

# 导入 DuckDuckGo 搜索的 Python 客户端库
from duckduckgo_search import DDGS

# 导入项目自定义的搜索相关模块
# SearchItem 是搜索结果的数据模型，WebSearchEngine 是搜索引擎基类
from app.tool.search.base import SearchItem, WebSearchEngine


# DuckDuckGo 搜索引擎实现类，继承自 WebSearchEngine
class DuckDuckGoSearchEngine(WebSearchEngine):
    # 执行搜索并返回格式化结果
    # query: 搜索关键词
    # num_results: 返回结果数量，默认为 10
    # 返回值: List[SearchItem]，包含格式化后的搜索结果
    def perform_search(
        self, query: str, num_results: int = 10, *args, **kwargs
    ) -> List[SearchItem]:
        """
        DuckDuckGo 搜索引擎。

        返回符合 SearchItem 模型格式的搜索结果。
        """
        # 使用 DDGS 客户端执行搜索，获取原始结果
        raw_results = DDGS().text(query, max_results=num_results)

        # 初始化结果列表
        results = []

        # 遍历原始结果，逐项格式化
        for i, item in enumerate(raw_results):
            # 如果结果是字符串类型（仅 URL）
            if isinstance(item, str):
                results.append(
                    SearchItem(
                        title=f"DuckDuckGo Result {i + 1}", url=item, description=None
                    )
                )
            # 如果结果是字典类型（包含标题、URL、描述）
            elif isinstance(item, dict):
                results.append(
                    SearchItem(
                        title=item.get("title", f"DuckDuckGo Result {i + 1}"),
                        url=item.get("href", ""),
                        description=item.get("body", None),
                    )
                )
            # 其他类型（尝试通过属性访问）
            else:
                try:
                    results.append(
                        SearchItem(
                            title=getattr(item, "title", f"DuckDuckGo Result {i + 1}"),
                            url=getattr(item, "href", ""),
                            description=getattr(item, "body", None),
                        )
                    )
                except Exception:
                    # 兜底逻辑，将结果转换为字符串
                    results.append(
                        SearchItem(
                            title=f"DuckDuckGo Result {i + 1}",
                            url=str(item),
                            description=None,
                        )
                    )

        return results
