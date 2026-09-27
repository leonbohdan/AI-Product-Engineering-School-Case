"""Wikipedia Trend Analyzer scripts package."""

from .topic_resolver import (
    ResolvedArticle,
    TopicResolutionResult,
    TopicResolver,
    parse_override_arg,
)
from .trend_analyzer import (
    GrowthMetrics,
    MultiLanguageComparisonResult,
    ReliabilityBreakdown,
    SpikeEvent,
    SpikeMetrics,
    TrendAnalysisResult,
    TrendAnalyzer,
    VolumeMetrics,
)
from .wikimedia_client import (
    ArticleNotFoundError,
    DailyPageView,
    PageViewsResult,
    RateLimitError,
    WikimediaAPIError,
    WikimediaClient,
)

__all__ = [
    "ArticleNotFoundError",
    "DailyPageView",
    "GrowthMetrics",
    "MultiLanguageComparisonResult",
    "PageViewsResult",
    "RateLimitError",
    "ReliabilityBreakdown",
    "ResolvedArticle",
    "SpikeEvent",
    "SpikeMetrics",
    "TopicResolutionResult",
    "TopicResolver",
    "TrendAnalysisResult",
    "TrendAnalyzer",
    "VolumeMetrics",
    "WikimediaAPIError",
    "WikimediaClient",
    "parse_override_arg",
]
