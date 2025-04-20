from typing import List  # 导入List类型，用于类型注解

from googlesearch import search  # 导入Google搜索库，用于执行搜索

from app.tool.search.base import SearchItem, WebSearchEngine  # 导入基础搜索类和结果模型


class GoogleSearchEngine(WebSearchEngine):
    """
    Google搜索引擎实现类，继承自WebSearchEngine。
    功能：通过Google搜索API获取搜索结果，并格式化为SearchItem对象。
    """

    def perform_search(
        self, query: str, num_results: int = 10, *args, **kwargs
    ) -> List[SearchItem]:
        """
        执行Google搜索并返回格式化结果。

        参数:
            query (str): 搜索关键词。
            num_results (int): 返回结果数量，默认为10。
            *args, **kwargs: 其他可选参数。

        返回值:
            List[SearchItem]: 格式化后的搜索结果列表。
        """
        raw_results = search(query, num_results=num_results, advanced=True)  # 调用Google搜索API获取原始结果

        results = []  # 存储格式化后的搜索结果
        for i, item in enumerate(raw_results):  # 遍历原始结果
            if isinstance(item, str):  # 如果结果是字符串（仅URL）
                # 构造一个默认的SearchItem对象
                results.append(
                    {"title": f"Google Result {i+1}", "url": item, "description": ""}
                )
            else:  # 如果结果是包含标题、URL和描述的完整对象
                results.append(
                    SearchItem(
                        title=item.title, url=item.url, description=item.description
                    )
                )

        return results  # 返回格式化后的结果