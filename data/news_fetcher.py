"""
新闻与舆情采集模块
数据源: RSS Feed, NewsAPI, 多源聚合
"""
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

import feedparser
import requests
from loguru import logger

from config.settings import DataConfig
from data.db import DatabaseManager


class NewsSentimentFetcher:
    """新闻与情绪分析采集器"""

    def __init__(self, db: DatabaseManager = None):
        self.db = db or DatabaseManager()
        self.news_api_key = DataConfig.NEWS_API_KEY

        # 财经新闻RSS源
        self.rss_feeds = {
            "reuters_business": "https://feeds.reuters.com/reuters/businessNews",
            "reuters_technology": "https://feeds.reuters.com/reuters/technologyNews",
            "cnbc": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10001147",
            "marketwatch": "http://feeds.marketwatch.com/marketwatch/topstories/",
            "seeking_alpha": "https://seekingalpha.com/market_currents.xml",
            "yahoo_finance": "https://finance.yahoo.com/news/rssindex",
        }

        # 行业关键词映射
        self.sector_keywords = {
            "tech": ["AI", "chip", "semiconductor", "software", "cloud", "cybersecurity", "tech"],
            "healthcare": ["drug", "FDA", "clinical", "biotech", "pharma", "healthcare", "medical"],
            "finance": ["bank", "interest rate", "Fed", "fintech", "credit", "lending"],
            "energy": ["oil", "gas", "OPEC", "renewable", "energy", "solar", "EV"],
            "consumer": ["retail", "consumer", "spending", "inflation", "CPI"],
        }

        # 宏观事件关键词
        self.macro_keywords = [
            "FOMC", "Fed", "interest rate", "CPI", "GDP", "unemployment",
            "nonfarm", "inflation", "recession", "trade war", "tariff",
            "geopolitical", "sanctions", "OPEC", "treasury yield"
        ]

    def fetch_rss_news(self, feed_url: str, limit: int = 20) -> list[dict]:
        """从RSS源获取新闻"""
        articles = []
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:limit]:
                article = {
                    "title": entry.get("title", ""),
                    "source": entry.get("source", {}).get("title", "") if "source" in entry else "",
                    "url": entry.get("link", ""),
                    "published_at": self._parse_rss_date(entry.get("published", "")),
                    "content": entry.get("summary", entry.get("description", "")),
                }
                articles.append(article)
        except Exception as e:
            logger.warning(f"RSS获取失败 {feed_url}: {e}")
        return articles

    def fetch_newsapi(self, query: str, days: int = 1, page_size: int = 50) -> list[dict]:
        """从NewsAPI获取新闻"""
        if not self.news_api_key:
            logger.warning("NewsAPI key未配置，跳过")
            return []

        articles = []
        try:
            url = "https://newsapi.org/v2/everything"
            params = {
                "q": query,
                "apiKey": self.news_api_key,
                "language": "en",
                "sortBy": "publishedAt",
                "pageSize": page_size,
                "from": (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d"),
            }
            response = requests.get(url, params=params, timeout=10)
            data = response.json()

            if data.get("status") == "ok":
                for article in data.get("articles", []):
                    articles.append({
                        "title": article.get("title", ""),
                        "source": article.get("source", {}).get("name", ""),
                        "url": article.get("url", ""),
                        "published_at": self._parse_iso_date(article.get("publishedAt", "")),
                        "content": article.get("description", ""),
                    })
        except Exception as e:
            logger.warning(f"NewsAPI获取失败: {e}")
        return articles

    def fetch_yahoo_news(self, symbol: str) -> list[dict]:
        """从Yahoo Finance获取股票相关新闻"""
        articles = []
        try:
            url = f"https://query2.finance.yahoo.com/v1/finance/search"
            params = {
                "q": symbol,
                "quotesCount": 0,
                "newsCount": 20,
                "enableFuzzyQuery": False,
            }
            headers = {"User-Agent": "Mozilla/5.0"}
            response = requests.get(url, params=params, headers=headers, timeout=10)
            data = response.json()

            for item in data.get("news", []):
                articles.append({
                    "symbol": symbol,
                    "title": item.get("title", ""),
                    "source": item.get("publisher", ""),
                    "url": item.get("link", ""),
                    "published_at": self._parse_timestamp(item.get("providerPublishTime", 0)),
                    "content": "",
                })
        except Exception as e:
            logger.warning(f"Yahoo新闻获取失败 {symbol}: {e}")
        return articles

    def analyze_sentiment(self, text: str) -> dict:
        """
        情绪分析
        返回: {score: -1~1, label: positive/negative/neutral, confidence: 0~1}
        """
        if not text or len(text.strip()) < 5:
            return {"score": 0.0, "label": "neutral", "confidence": 0.0}

        try:
            from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
            analyzer = SentimentIntensityAnalyzer()
            scores = analyzer.polarity_scores(text)
            compound = scores["compound"]

            if compound >= 0.05:
                label = "positive"
            elif compound <= -0.05:
                label = "negative"
            else:
                label = "neutral"

            confidence = abs(compound)
            return {"score": compound, "label": label, "confidence": confidence}

        except ImportError:
            # 简单关键词匹配作为后备
            positive_words = ["surge", "rally", "beat", "exceed", "growth", "profit", "gain",
                            "upgrade", "bullish", "strong", "record", "breakthrough", "soar"]
            negative_words = ["crash", "plunge", "miss", "decline", "loss", "drop", "fall",
                            "downgrade", "bearish", "weak", "recession", "cut", "slump"]

            text_lower = text.lower()
            pos_count = sum(1 for w in positive_words if w in text_lower)
            neg_count = sum(1 for w in negative_words if w in text_lower)

            total = pos_count + neg_count
            if total == 0:
                return {"score": 0.0, "label": "neutral", "confidence": 0.0}

            score = (pos_count - neg_count) / total
            label = "positive" if score > 0 else ("negative" if score < 0 else "neutral")
            return {"score": score, "label": label, "confidence": min(abs(score), 1.0)}

    def determine_relevance(self, article: dict, symbol: str) -> float:
        """判断新闻与股票的相关度 (0-1)"""
        text = f"{article.get('title', '')} {article.get('content', '')}".lower()
        symbol_lower = symbol.lower()

        # 直接提及
        if symbol_lower in text:
            return 0.9
        # 公司常见名
        company_names = {
            "AAPL": ["apple", "tim cook", "iphone", "ipad", "mac"],
            "MSFT": ["microsoft", "satya nadella", "azure", "windows", "office"],
            "GOOGL": ["google", "alphabet", "sundar pichai", "android", "youtube"],
            "NVDA": ["nvidia", "jensen huang", "gpu", "cuda", "data center"],
            "AMZN": ["amazon", "bezos", "aws", "prime", "whole foods"],
            "META": ["meta", "zuckerberg", "facebook", "instagram", "whatsapp"],
            "TSLA": ["tesla", "elon musk", "model 3", "model y", "cybertruck"],
        }
        for keyword in company_names.get(symbol, []):
            if keyword in text:
                return 0.7

        # 行业相关
        return 0.3  # 默认低相关

    def collect_and_store_news(self, symbol: str) -> list[dict]:
        """
        采集并存储所有新闻源
        """
        all_articles = []

        # 1. Yahoo Finance新闻
        yahoo_articles = self.fetch_yahoo_news(symbol)
        all_articles.extend(yahoo_articles)

        # 2. NewsAPI
        newsapi_articles = self.fetch_newsapi(symbol, days=1)
        for a in newsapi_articles:
            a["symbol"] = symbol
        all_articles.extend(newsapi_articles)

        # 3. 分析情绪并存储
        stored_count = 0
        for article in all_articles:
            # 情绪分析
            text = f"{article.get('title', '')} {article.get('content', '')}"
            sentiment = self.analyze_sentiment(text)
            article["sentiment_score"] = sentiment["score"]
            article["sentiment_label"] = sentiment["label"]

            # 相关度
            article["relevance_score"] = self.determine_relevance(article, symbol)

            # 存储
            try:
                self.db.save_news(article)
                stored_count += 1
            except Exception:
                pass  # 可能重复

        logger.info(f"{symbol} 新闻采集完成: 获取{len(all_articles)}条, 存储{stored_count}条")
        return all_articles

    def get_market_sentiment_summary(self, symbol: str) -> dict:
        """
        获取市场情绪汇总
        """
        articles = self.db.get_news_for_symbol(symbol, limit=30)

        if not articles:
            return {"symbol": symbol, "sentiment": "neutral", "score": 0.0, "article_count": 0}

        scores = [a.sentiment_score for a in articles if a.sentiment_score is not None]
        if not scores:
            return {"symbol": symbol, "sentiment": "neutral", "score": 0.0, "article_count": len(articles)}

        avg_score = sum(scores) / len(scores)
        # 加权近期新闻
        recent_scores = scores[:5]
        weighted_score = 0.6 * (sum(recent_scores) / len(recent_scores)) + 0.4 * avg_score

        if weighted_score > 0.1:
            label = "positive"
        elif weighted_score < -0.1:
            label = "negative"
        else:
            label = "neutral"

        return {
            "symbol": symbol,
            "sentiment": label,
            "score": round(weighted_score, 3),
            "article_count": len(articles),
            "positive_count": sum(1 for s in scores if s > 0.05),
            "negative_count": sum(1 for s in scores if s < -0.05),
            "neutral_count": sum(1 for s in scores if -0.05 <= s <= 0.05),
        }

    def fetch_macro_events(self) -> list[dict]:
        """
        获取宏观经济日历事件
        """
        events = []
        try:
            # 从investing.com经济日历获取（通过RSS或其他方式）
            url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                data = response.json()
                for item in data:
                    event = {
                        "event_name": item.get("title", ""),
                        "country": item.get("country", ""),
                        "event_type": self._classify_macro_event(item.get("title", "")),
                        "scheduled_at": self._parse_iso_date(item.get("date", "")),
                        "actual_value": self._parse_macro_value(item.get("actual", "")),
                        "forecast_value": self._parse_macro_value(item.get("forecast", "")),
                        "previous_value": self._parse_macro_value(item.get("previous", "")),
                        "impact": item.get("impact", "Low").lower(),
                    }
                    events.append(event)
        except Exception as e:
            logger.warning(f"获取宏观事件失败: {e}")

        logger.info(f"获取宏观事件: {len(events)} 条")
        return events

    def _classify_macro_event(self, title: str) -> str:
        """分类宏观事件类型"""
        title_lower = title.lower()
        if "cpi" in title_lower or "inflation" in title_lower:
            return "CPI"
        if "gdp" in title_lower:
            return "GDP"
        if "fomc" in title_lower or "fed" in title_lower or "interest rate" in title_lower:
            return "FOMC"
        if "unemployment" in title_lower or "nonfarm" in title_lower or "jobs" in title_lower:
            return "Employment"
        if "pmi" in title_lower:
            return "PMI"
        return "Other"

    @staticmethod
    def _parse_rss_date(date_str: str) -> Optional[datetime]:
        """解析RSS日期格式"""
        if not date_str:
            return None
        try:
            from dateutil import parser
            return parser.parse(date_str)
        except Exception:
            return None

    @staticmethod
    def _parse_iso_date(date_str: str) -> Optional[datetime]:
        """解析ISO日期格式"""
        if not date_str:
            return None
        try:
            return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        except Exception:
            return None

    @staticmethod
    def _parse_timestamp(ts) -> Optional[datetime]:
        """解析Unix时间戳"""
        if not ts:
            return None
        try:
            return datetime.fromtimestamp(int(ts), tz=timezone.utc)
        except Exception:
            return None

    @staticmethod
    def _parse_macro_value(val_str: str) -> Optional[float]:
        """解析宏观指标数值"""
        if not val_str or val_str in ("", "-"):
            return None
        try:
            return float(re.sub(r"[,%]", "", val_str))
        except ValueError:
            return None
