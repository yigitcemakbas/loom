"""Import hub: every ORM model gets imported here so `import app.models`
registers all of them on Base.metadata, this is what Alembic autogenerate
(and anything else needing the full metadata) should import.
"""

from app.models.brief import CompanyBrief, Stance  # noqa: F401
from app.models.company import Company  # noqa: F401
from app.models.document import RawDocument, SourceType  # noqa: F401
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
