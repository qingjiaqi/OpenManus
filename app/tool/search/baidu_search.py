from typing import List  # 导入类型注解模块，用于类型提示

from baidusearch.baidusearch import search  # 导入百度搜索模块，用于执行搜索

from app.tool.search.base import SearchItem, WebSearchEngine  # 导入基础搜索类和结果模型


class BaiduSearchEngine(WebSearchEngine):
    """百度搜索引擎实现类，继承自基础搜索类 WebSearchEngine。"""

    def perform_search(
        self, query: str, num_results: int = 10, *args, **kwargs
    ) -> List[SearchItem]:
        """执行百度搜索并返回格式化结果。

        Args:
            query: 搜索关键词。
            num_results: 返回结果数量，默认为10。
            *args: 可变位置参数。
            **kwargs: 可变关键字参数。

        Returns:
            List[SearchItem]: 格式化后的搜索结果列表。
        """
        raw_results = search(query, num_results=num_results)  # 调用百度搜索接口获取原始结果

        # 将原始结果转换为 SearchItem 格式
        results = []
        for i, item in enumerate(raw_results):
            if isinstance(item, str):
                # 如果结果是字符串（仅URL），生成基础结果
                results.append(
                    SearchItem(title=f"Baidu Result {i+1}", url=item, description=None)
                )
            elif isinstance(item, dict):
                # 如果结果是字典（包含详细信息），提取标题、URL和描述
                results.append(
                    SearchItem(
                        title=item.get("title", f"Baidu Result {i+1}"),
                        url=item.get("url", ""),
                        description=item.get("abstract", None),
                    )
                )
            else:
                # 尝试直接获取对象的属性
                try:
                    results.append(
                        SearchItem(
                            title=getattr(item, "title", f"Baidu Result {i+1}"),
                            url=getattr(item, "url", ""),
                            description=getattr(item, "abstract", None),
                        )
                    )
                except Exception:
                    # 如果无法获取属性，生成基础结果
                    results.append(
                        SearchItem(
                            title=f"Baidu Result {i+1}", url=str(item), description=None
                        )
                    )

        return results
