"""Import hub: every ORM model gets imported here so `import app.models`
registers all of them on Base.metadata, this is what Alembic autogenerate
(and anything else needing the full metadata) should import.
"""

from app.models.account import LoginCode, Position, Session, User  # noqa: F401
from app.models.api_key import ApiKey  # noqa: F401
from app.models.assessment_label import AssessmentLabel  # noqa: F401
from app.models.brief import CompanyBrief, Stance  # noqa: F401
from app.models.company import Company, CompanyTier  # noqa: F401
from app.models.corpus_term import CorpusTerm  # noqa: F401
from app.models.document import RawDocument, SourceType  # noqa: F401
from app.models.event_assessment import EventAssessment  # noqa: F401
from app.models.experiment import AgentDecision  # noqa: F401
from app.models.exposure import CompanyExposure  # noqa: F401
from app.models.factor import COMPOSITE_KEY, FactorScore  # noqa: F401
from app.models.price_bar import PriceBar  # noqa: F401
from app.models.reliability import SignalReliability  # noqa: F401
from app.models.prior import CompanyPrior  # noqa: F401
from app.models.search import DocumentSearchIndex  # noqa: F401
from app.models.structured_fact import FactType, StructuredFact  # noqa: F401
from app.models.signal import (  # noqa: F401
    AnalysisStatus,
    DocumentAnalysis,
    Signal,
    SignalType,
)
from app.models.usage import LLMUsageRun  # noqa: F401
from app.models.watchlist import Watchlist, WatchlistItem  # noqa: F401
