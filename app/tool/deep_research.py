# 导入必要的库和模块
import asyncio  # 异步IO支持
import json  # JSON数据处理
import re  # 正则表达式支持
import time  # 时间处理
from typing import List, Optional, Set  # 类型注解支持

from pydantic import BaseModel, ConfigDict, Field, model_validator  # 数据模型和验证

from app.exceptions import ToolError  # 自定义异常
from app.llm import LLM  # 大语言模型接口
from app.logger import logger  # 日志记录
from app.schema import ToolChoice  # 工具选择模型
from app.tool.base import BaseTool, ToolResult  # 基础工具类
from app.tool.web_search import SearchResult, WebSearch  # 网络搜索工具


# 用于与大语言模型交互的提示模板
OPTIMIZE_QUERY_PROMPT = """
You are a research assistant helping to optimize a search query for web research.
Your task is to reformulate the given query to be more effective for web searches.
Make it specific, use relevant keywords, and ensure it's clear and concise.

Original query: {query}

Provide only the optimized query text without any explanation or additional formatting.
"""

EXTRACT_INSIGHTS_PROMPT = """
Analyze the following content and extract key insights related to the research query.
For each insight, assess its relevance to the query on a scale of 0.0 to 1.0.

Research query: {query}
Content to analyze:
{content}

Extract up to 3 most important insights from this content. For each insight:
1. Provide the insight content
2. Provide relevance score (0.0-1.0)
"""

GENERATE_FOLLOW_UPS_PROMPT = """
Based on the insights discovered so far, generate follow-up research queries to explore gaps or related areas.
These should help deepen our understanding of the topic.

Original query: {original_query}
Current query: {current_query}
Key insights so far:
{insights}

Generate up to 3 specific follow-up queries that would help address gaps in our current knowledge.
Each query should be concise and focused on a specific aspect of the research topic.
"""

# 用于解析洞察结果的常量
DEFAULT_RELEVANCE_SCORE = 1.0  # 默认相关性分数
FALLBACK_RELEVANCE_SCORE = 0.7  # 回退相关性分数
FALLBACK_CONTENT_LIMIT = 500  # 回退内容长度限制
# 匹配洞察内容的正则表达式（数字、-、*、•开头）
INSIGHT_MARKER_PATTERN = re.compile(r"^\s*(?:\d+\.|-|\*|•)\s*(.*)")
# 匹配相关性分数的正则表达式（不区分大小写）
RELEVANCE_SCORE_PATTERN = re.compile(r"relevance.*?:.*?(\d\.?\d*)", re.IGNORECASE)


class ResearchInsight(BaseModel):
    """
    表示在研究过程中发现的单个洞察结果。

    属性:
        content (str): 洞察内容。
        source_url (str): 发现该洞察的URL。
        source_title (Optional[str]): 来源标题（可选）。
        relevance_score (float): 相关性评分（0.0-1.0）。
    """

    model_config = ConfigDict(frozen=True)  # 使洞察结果不可变

    content: str = Field(description="洞察内容")
    source_url: str = Field(description="发现该洞察的URL")
    source_title: Optional[str] = Field(default=None, description="来源标题")
    relevance_score: float = Field(
        default=1.0, description="相关性评分（0.0-1.0）", ge=0.0, le=1.0
    )

    def __str__(self) -> str:
        """
        格式化洞察结果为字符串，包含来源信息。

        返回:
            str: 格式化后的字符串。
        """
        source = self.source_title or self.source_url
        return f"{self.content} [Source: {source}]"


class ResearchContext(BaseModel):
    """
    用于跟踪研究进度的上下文信息。

    属性:
        query (str): 原始研究查询。
        insights (List[ResearchInsight]): 已发现的洞察结果列表。
        follow_up_queries (List[str]): 生成的后续查询列表。
        visited_urls (Set[str]): 已访问的URL集合。
        current_depth (int): 当前研究深度。
        max_depth (int): 最大研究深度。
    """

    query: str = Field(description="原始研究查询")
    insights: List[ResearchInsight] = Field(
        default_factory=list, description="已发现的洞察结果列表"
    )
    follow_up_queries: List[str] = Field(
        default_factory=list, description="生成的后续查询列表"
    )
    visited_urls: Set[str] = Field(
        default_factory=set, description="已访问的URL集合"
    )
    current_depth: int = Field(
        default=0, description="当前研究深度", ge=0
    )
    max_depth: int = Field(
        default=2, description="最大研究深度", ge=1
    )


class ResearchSummary(ToolResult):
    """
    表示深度研究结果的综合摘要。

    属性:
        query (str): 原始研究查询。
        insights (List[ResearchInsight]): 已发现的洞察结果列表。
        visited_urls (Set[str]): 已访问的URL集合。
        depth_reached (int): 达到的最大研究深度。
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    query: str = Field(description="原始研究查询")
    insights: List[ResearchInsight] = Field(
        default_factory=list, description="已发现的洞察结果列表"
    )
    visited_urls: Set[str] = Field(
        default_factory=set, description="已访问的URL集合"
    )
    depth_reached: int = Field(
        default=0, description="达到的最大研究深度", ge=0
    )

    @model_validator(mode="after")
    def populate_output(self) -> "ResearchSummary":
        """
        在验证后填充输出字段，格式化摘要内容。

        返回:
            ResearchSummary: 填充后的摘要对象。
        """
        # 按相关性分组和排序洞察结果
        grouped_insights = {
            "Key Findings": [i for i in self.insights if i.relevance_score >= 0.8],
            "Additional Information": [
                i for i in self.insights if 0.5 <= i.relevance_score < 0.8
            ],
            "Supplementary Information": [
                i for i in self.insights if i.relevance_score < 0.5
            ],
        }

        sections = [
            f"# Research: {self.query}\n",
            f"**Sources**: {len(self.visited_urls)} | **Depth**: {self.depth_reached + 1}\n",
        ]

        for section_title, insights in grouped_insights.items():
            if insights:
                sections.append(f"## {section_title}")
                for i, insight in enumerate(insights, 1):
                    sections.extend(
                        [
                            insight.content,
                            f"> Source: [{insight.source_title or 'Link'}]({insight.source_url})\n",
                        ]
                    )

        # 将格式化后的字符串赋给继承自ToolResult的'output'字段
        self.output = "\n".join(sections)
        return self


class DeepResearch(BaseTool):
    """
    高级研究工具，通过迭代的网络搜索和内容分析探索主题。

    属性:
        name (str): 工具名称。
        description (str): 工具描述。
        parameters (dict): 工具参数定义。
        search_tool (WebSearch): 网络搜索工具依赖。
        llm (LLM): 大语言模型依赖。
    """

    name: str = "deep_research"
    description: str = """
    通过多级网络搜索和内容分析对主题进行全面研究。
    返回带有来源归属和相关性评分的结构化摘要。
    """
    parameters: dict = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "要调查的研究问题或主题。",
            },
            "max_depth": {
                "type": "integer",
                "description": "迭代研究的最大深度（1-5）。默认值为2。",
                "default": 2,
            },
            "results_per_search": {
                "type": "integer",
                "description": "每次搜索要分析的结果数量（1-20）。默认值为5。",
                "default": 5,
            },
            "max_insights": {
                "type": "integer",
                "description": "返回的最大洞察数量。默认值为20。",
                "default": 20,
            },
            "time_limit_seconds": {
                "type": "integer",
                "description": "最大执行时间（秒）。默认值为120。",
                "default": 120,
            },
        },
        "required": ["query"],
    }

    # 依赖注入，便于测试
    search_tool: WebSearch = Field(default_factory=WebSearch)
    llm: LLM = Field(default_factory=LLM)

    async def execute(
        self,
        query: str,
        max_depth: int = 2,
        results_per_search: int = 5,
        max_insights: int = 20,
        time_limit_seconds: int = 120,
    ) -> ResearchSummary:
        """
        执行对给定查询的深度研究。

        参数:
            query (str): 研究查询。
            max_depth (int): 最大研究深度。
            results_per_search (int): 每次搜索的结果数量。
            max_insights (int): 最大洞察数量。
            time_limit_seconds (int): 执行时间限制（秒）。

        返回:
            ResearchSummary: 研究结果的综合摘要。
        """
        # 规范化参数
        max_depth = max(1, min(max_depth, 5))
        results_per_search = max(1, min(results_per_search, 20))

        # 初始化研究上下文并设置截止时间
        context = ResearchContext(query=query, max_depth=max_depth)
        deadline = time.time() + time_limit_seconds

        try:
            # 使用优化后的查询启动研究过程
            optimized_query = await self._generate_optimized_query(query)
            await self._research_graph(
                context=context,
                query=optimized_query,
                results_count=results_per_search,
                deadline=deadline,
            )
        except ToolError as e:
            logger.error(f"Research error: {str(e)}")

        # 准备最终摘要
        return ResearchSummary(
            query=query,
            insights=sorted(
                context.insights, key=lambda x: x.relevance_score, reverse=True
            )[:max_insights],
            visited_urls=context.visited_urls,
            depth_reached=context.current_depth,
        )

    async def _generate_optimized_query(self, query: str) -> str:
        """
        使用大语言模型生成优化的搜索查询。

        参数:
            query (str): 原始查询。

        返回:
            str: 优化后的查询。
        """
        try:
            prompt = OPTIMIZE_QUERY_PROMPT.format(query=query)
            response = await self.llm.ask_tool(
                [{"role": "user", "content": prompt}],
                tools=[
                    {
                        "type": "function",
                        "function": {
                            "name": "optimize_query",
                            "description": "生成优化的搜索查询",
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "query": {
                                        "type": "string",
                                        "description": "优化的搜索查询",
                                    }
                                },
                                "required": ["query"],
                            },
                        },
                    }
                ],
                tool_choice=ToolChoice.REQUIRED,
                stream=False,
            )

            # 从工具调用响应中提取查询
            if response and response.tool_calls and len(response.tool_calls) > 0:
                tool_call = response.tool_calls[0]
                arguments = json.loads(tool_call.function.arguments)
                optimized_query = arguments.get("query", "")
            else:
                # 如果工具调用失败，回退到原始查询
                logger.warning("Tool call failed to return a valid response")
                return query

            if not optimized_query:
                logger.warning("Generated empty optimized query, using original")
                return query

            logger.info(f"Optimized query: '{optimized_query}'")
            return optimized_query
        except Exception as e:
            logger.warning(f"Failed to optimize query: {str(e)}")
            return query  # 出错时回退到原始查询

    async def _research_graph(
        self,
        context: ResearchContext,
        query: str,
        results_count: int,
        deadline: float,
    ) -> None:
        """
        运行完整的研究周期（搜索、分析、生成后续查询）。

        参数:
            context (ResearchContext): 研究上下文。
            query (str): 当前查询。
            results_count (int): 每次搜索的结果数量。
            deadline (float): 截止时间戳。
        """
        # 检查终止条件
        if time.time() >= deadline or context.current_depth >= context.max_depth:
            return

        # 记录当前研究步骤
        logger.info(f"Research cycle at depth {context.current_depth + 1}")

        # 1. 网络搜索
        search_results = await self._search_web(query, results_count)
        if not search_results:
            return

        # 2. 提取洞察
        new_insights = await self._extract_insights(
            context, search_results, context.query, deadline
        )
        if not new_insights:
            return

        # 3. 生成后续查询
        follow_up_queries = await self._generate_follow_ups(
            new_insights, query, context.query
        )
        context.follow_up_queries.extend(follow_up_queries)

        # 更新深度并进入下一级
        context.current_depth += 1

        # 4. 使用后续查询继续研究
        if follow_up_queries and context.current_depth < context.max_depth:
            tasks = []  # 创建任务列表
            for follow_up in follow_up_queries[:2]:  # 限制分支因子
                if time.time() >= deadline:
                    break

                # 为递归研究调用创建协程
                task = self._research_graph(
                    context=context,
                    query=follow_up,
                    results_count=max(1, results_count - 1),  # 减少结果数量
                    deadline=deadline,
                )
                tasks.append(task)  # 将任务添加到列表

            # 并发运行所有任务
            if tasks:
                await asyncio.gather(*tasks)

    async def _search_web(self, query: str, results_count: int) -> List[SearchResult]:
        """
        对给定查询执行网络搜索。

        参数:
            query (str): 搜索查询。
            results_count (int): 返回的结果数量。

        返回:
            List[SearchResult]: 搜索结果列表。
        """
        search_response = await self.search_tool.execute(
            query=query, num_results=results_count, fetch_content=True
        )
        return [] if search_response.error else search_response.results

    async def _extract_insights(
        self,
        context: ResearchContext,
        results: List[SearchResult],
        original_query: str,
        deadline: float,
    ) -> List[ResearchInsight]:
        """
        从搜索结果中提取洞察。

        参数:
            context (ResearchContext): 研究上下文。
            results (List[SearchResult]): 搜索结果列表。
            original_query (str): 原始查询。
            deadline (float): 截止时间戳。

        返回:
            List[ResearchInsight]: 提取的洞察列表。
        """
        all_insights = []

        for rst in results:
            # 跳过已访问的URL或超时的情况
            if rst.url in context.visited_urls or time.time() >= deadline:
                continue

            context.visited_urls.add(rst.url)

            # 跳过无可用内容的情况
            if not rst.raw_content:
                continue

            # 使用大语言模型提取洞察
            insights = await self._analyze_content(
                content=rst.raw_content[:10000],  # 限制内容大小
                url=rst.url,
                title=rst.title,
                query=original_query,
            )

            all_insights.extend(insights)
            context.insights.extend(insights)

            # 记录发现的洞察
            logger.info(f"Extracted {len(insights)} insights from {rst.url}")

        return all_insights

    async def _generate_follow_ups(
        self, insights: List[ResearchInsight], current_query: str, original_query: str
    ) -> List[str]:
        """
        基于洞察生成后续查询。

        参数:
            insights (List[ResearchInsight]): 洞察列表。
            current_query (str): 当前查询。
            original_query (str): 原始查询。

        返回:
            List[str]: 后续查询列表。
        """
        if not insights:
            return []

        # 格式化洞察内容用于提示
        insights_text = "\n".join([f"- {insight.content}" for insight in insights[:5]])

        # 创建生成后续查询的提示
        prompt = GENERATE_FOLLOW_UPS_PROMPT.format(
            original_query=original_query,
            current_query=current_query,
            insights=insights_text,
        )

        # 使用大语言模型生成后续查询
        response = await self.llm.ask_tool(
            [{"role": "user", "content": prompt}],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "generate_follow_ups",
                        "description": "基于研究洞察生成后续查询",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "follow_up_queries": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "后续查询列表（最多3个）",
                                    "maxItems": 3,
                                }
                            },
                            "required": ["follow_up_queries"],
                        },
                    },
                }
            ],
            tool_choice=ToolChoice.REQUIRED,
            stream=False,
        )

        # 从工具响应中提取查询
        queries = []
        if response and response.tool_calls and len(response.tool_calls) > 0:
            tool_call = response.tool_calls[0]
            arguments = json.loads(tool_call.function.arguments)
            queries = arguments.get("follow_up_queries", [])

        # 确保返回不超过3个查询
        return queries[:3]

    async def _analyze_content(
        self, content: str, url: str, title: str, query: str
    ) -> List[ResearchInsight]:
        """
        从内容中提取与查询相关的洞察。

        参数:
            content (str): 待分析的内容。
            url (str): 内容来源的URL。
            title (str): 内容标题。
            query (str): 研究查询。

        返回:
            List[ResearchInsight]: 提取的洞察列表。
        """
        prompt = EXTRACT_INSIGHTS_PROMPT.format(
            query=query, content=content[:5000]  # 限制内容大小
        )

        response = await self.llm.ask_tool(
            [{"role": "user", "content": prompt}],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "extract_insights",
                        "description": "从内容中提取带有相关性评分的洞察",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "insights": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "content": {
                                                "type": "string",
                                                "description": "洞察内容",
                                            },
                                            "relevance_score": {
                                                "type": "number",
                                                "description": "相关性评分（0.0-1.0）",
                                                "minimum": 0.0,
                                                "maximum": 1.0,
                                            },
                                        },
                                        "required": ["content", "relevance_score"],
                                    },
                                    "description": "从内容中提取的关键洞察列表",
                                    "maxItems": 3,
                                }
                            },
                            "required": ["insights"],
                        },
                    },
                }
            ],
            tool_choice=ToolChoice.REQUIRED,
            stream=False,
        )

        insights = []

        # 处理结构化的JSON响应
        if response and response.tool_calls and len(response.tool_calls) > 0:
            tool_call = response.tool_calls[0]
            arguments = json.loads(tool_call.function.arguments)
            extracted_insights = arguments.get("insights", [])

            for insight_data in extracted_insights:
                insights.append(
                    ResearchInsight(
                        content=insight_data.get("content", ""),
                        source_url=url,
                        source_title=title,
                        relevance_score=insight_data.get(
                            "relevance_score", FALLBACK_RELEVANCE_SCORE
                        ),
                    )
                )

        # 回退：如果未找到结构化洞察，使用回退方法
        if not insights:
            logger.warning(
                f"Could not parse structured insights from LLM response for {url}. Using fallback."
            )
            insights.append(
                ResearchInsight(
                    content=f"Failed to extract structured insights from content about {title or url}."[
                        :FALLBACK_CONTENT_LIMIT
                    ],
                    source_url=url,
                    source_title=title,
                    relevance_score=FALLBACK_RELEVANCE_SCORE,
                )
            )

        return insights


if __name__ == "__main__":
    """
    主程序入口，用于测试DeepResearch工具的功能。
    """
    deep_research = DeepResearch()
    result = asyncio.run(
        deep_research.execute(
            "What is deep learning", max_depth=1, results_per_search=2
        )
    )
    print(result)
