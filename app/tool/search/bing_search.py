from typing import List, Optional, Tuple  # 导入类型注解模块，用于类型提示

import requests  # 用于发送HTTP请求
from bs4 import BeautifulSoup  # 用于解析HTML内容

from app.logger import logger  # 导入日志模块
from app.tool.search.base import SearchItem, WebSearchEngine  # 导入基础搜索类和结果项


ABSTRACT_MAX_LENGTH = 300  # 摘要的最大长度限制

USER_AGENTS = [  # 用户代理列表，用于模拟不同浏览器访问
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/68.0.3440.106 Safari/537.36",
    "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Ubuntu Chromium/49.0.2623.108 Chrome/49.0.2623.108 Safari/537.36",
    "Mozilla/5.0 (Windows; U; Windows NT 5.1; pt-BR) AppleWebKit/533.3 (KHTML, like Gecko) QtWeb Internet Browser/3.7 http://www.QtWeb.net",
    "Mozilla/5.0 (Windows NT 6.1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/41.0.2228.0 Safari/537.36",
    "Mozilla/5.0 (Windows; U; Windows NT 5.1; en-US) AppleWebKit/532.2 (KHTML, like Gecko) ChromePlus/4.0.222.3 Chrome/4.0.222.3 Safari/532.2",
    "Mozilla/5.0 (Windows; U; Windows NT 5.1; en-US; rv:1.8.1.4pre) Gecko/20070404 K-Ninja/2.1.3",
    "Mozilla/5.0 (Future Star Technologies Corp.; Star-Blade OS; x86_64; U; en-US) iNet Browser 4.7",
    "Mozilla/5.0 (Windows; U; Windows NT 6.1; rv:2.2) Gecko/20110201",
    "Mozilla/5.0 (Windows; U; Windows NT 5.1; en-US; rv:1.8.1.13) Gecko/20080414 Firefox/2.0.0.13 Pogo/2.0.0.13.6866",
]

HEADERS = {  # HTTP请求头配置
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8",
    "Content-Type": "application/x-www-form-urlencoded",
    "User-Agent": USER_AGENTS[0],  # 使用第一个用户代理
    "Referer": "https://www.bing.com/",  # 引用来源
    "Accept-Encoding": "gzip, deflate",  # 支持的编码方式
    "Accept-Language": "zh-CN,zh;q=0.9",  # 语言偏好
}

BING_HOST_URL = "https://www.bing.com"  # Bing主页URL
BING_SEARCH_URL = "https://www.bing.com/search?q="  # Bing搜索URL


class BingSearchEngine(WebSearchEngine):  # Bing搜索引擎实现类
    session: Optional[requests.Session] = None  # 可选的requests会话对象

    def __init__(self, **data):
        """初始化Bing搜索工具，配置HTTP会话。"""
        super().__init__(**data)
        self.session = requests.Session()  # 创建会话
        self.session.headers.update(HEADERS)  # 更新会话头

    def _search_sync(self, query: str, num_results: int = 10) -> List[SearchItem]:
        """
        同步Bing搜索实现，获取搜索结果。

        参数:
            query (str): 搜索关键词。
            num_results (int, 可选): 返回结果的最大数量，默认为10。

        返回:
            List[SearchItem]: 包含标题、URL和描述的搜索结果列表。
        """
        if not query:  # 如果查询为空，返回空列表
            return []

        list_result = []  # 存储搜索结果
        first = 1  # 起始页码
        next_url = BING_SEARCH_URL + query  # 初始搜索URL

        while len(list_result) < num_results:  # 循环直到获取足够的结果
            data, next_url = self._parse_html(
                next_url, rank_start=len(list_result), first=first
            )
            if data:
                list_result.extend(data)  # 添加解析结果
            if not next_url:  # 如果没有下一页，退出循环
                break
            first += 10  # 更新页码

        return list_result[:num_results]  # 返回截取后的结果

    def _parse_html(
        self, url: str, rank_start: int = 0, first: int = 1
    ) -> Tuple[List[SearchItem], str]:
        """
        解析Bing搜索结果HTML，提取结果和下一页URL。

        返回:
            tuple: (搜索结果列表, 下一页URL或None)
        """
        try:
            res = self.session.get(url=url)  # 发送HTTP请求
            res.encoding = "utf-8"  # 设置编码
            root = BeautifulSoup(res.text, "lxml")  # 解析HTML

            list_data = []  # 存储解析结果
            ol_results = root.find("ol", id="b_results")  # 查找结果列表
            if not ol_results:  # 如果没有结果，返回空
                return [], None

            for li in ol_results.find_all("li", class_="b_algo"):  # 遍历每个结果项
                title = ""
                url = ""
                abstract = ""
                try:
                    h2 = li.find("h2")  # 查找标题
                    if h2:
                        title = h2.text.strip()  # 提取标题文本
                        url = h2.a["href"].strip()  # 提取URL

                    p = li.find("p")  # 查找摘要
                    if p:
                        abstract = p.text.strip()  # 提取摘要文本

                    if ABSTRACT_MAX_LENGTH and len(abstract) > ABSTRACT_MAX_LENGTH:
                        abstract = abstract[:ABSTRACT_MAX_LENGTH]  # 截断超长摘要

                    rank_start += 1  # 更新排名

                    # 创建SearchItem对象
                    list_data.append(
                        SearchItem(
                            title=title or f"Bing Result {rank_start}",
                            url=url,
                            description=abstract,
                        )
                    )
                except Exception:
                    continue

            next_btn = root.find("a", title="Next page")  # 查找下一页按钮
            if not next_btn:
                return list_data, None

            next_url = BING_HOST_URL + next_btn["href"]  # 构造下一页URL
            return list_data, next_url
        except Exception as e:
            logger.warning(f"Error parsing HTML: {e}")  # 记录解析错误
            return [], None

    def perform_search(
        self, query: str, num_results: int = 10, *args, **kwargs
    ) -> List[SearchItem]:
        """
        Bing搜索引擎实现。

        返回符合SearchItem模型的搜索结果。
        """
        return self._search_sync(query, num_results=num_results)
