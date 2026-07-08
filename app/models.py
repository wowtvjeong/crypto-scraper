from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class RawItem:
    """스크래핑 직후, AI 필터링 전 원본 아이템."""
    source: str            # 채널명 or 언론사명
    source_type: str       # "telegram" | "press"
    title: str
    content: str
    url: str
    published_at: Optional[str] = None


@dataclass
class ScoredArticle:
    """AI 필터링 후 최종 저장되는 기사."""
    source: str
    source_type: str
    title: str
    content: str
    url: str
    published_at: Optional[str]
    relevance_score: float
    is_crypto_related: bool
    reason: str
    collected_at: str = field(default_factory=lambda: datetime.now().isoformat())
