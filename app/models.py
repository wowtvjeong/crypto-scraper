from dataclasses import dataclass, field
from datetime import datetime, timezone
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
    # GitHub Actions 러너는 UTC로 동작하므로, 시간대 정보(+00:00)를 명시해야
    # 브라우저(대시보드)가 이걸 정확히 한국시간으로 변환해서 보여줄 수 있다.
    # 시간대 정보 없이 저장하면 브라우저가 "이미 로컬시간"으로 착각해서
    # 최대 9시간(KST 기준)까지 어긋나게 표시된다.
    collected_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
