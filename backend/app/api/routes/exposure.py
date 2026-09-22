"""The dependency graph, as a graph.

Served whole rather than per company because the interesting structure is not
any single edge, it is the clusters: which companies sit at the centre of a
sector and which sit at the edge of several. That only shows up when the whole
thing is drawn at once, and at a few hundred edges it is far cheaper to send
the lot than to have a client walk it a node at a time.
"""

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.api.deps import CompanyRepo, DbSession
from app.models.exposure import CompanyExposure

router = APIRouter(tags=["exposure"])


class ExposureNode(BaseModel):
    ticker: str
    name: str
    sector: str | None
    # How many tracked companies move when this one does.
    reach: int
    # How many companies this one is downstream of.
    upstream: int


class ExposureEdge(BaseModel):
    hub: str
    dependent: str
    mention_count: int


class ExposureGraph(BaseModel):
    nodes: list[ExposureNode]
    edges: list[ExposureEdge]


@router.get("/exposure", response_model=ExposureGraph)
def exposure_graph(
    db: DbSession,
    company_repo: CompanyRepo,
    min_mentions: int = Query(default=0, ge=0),
):
    """Every dependency edge, with node degrees precomputed.

    Degrees are computed here rather than in the client because they decide
    how the graph is drawn, and a client that derives them itself will get a
    different answer the moment it filters the edge list.
    """
    rows = db.query(CompanyExposure).all()
    companies = {c.id: c for c in company_repo.list_all()}

    edges: list[ExposureEdge] = []
    reach: dict[str, int] = {}
    upstream: dict[str, int] = {}
    involved: set[str] = set()

    for row in rows:
        if row.mention_count < min_mentions:
            continue
        hub = companies.get(row.hub_company_id)
        dependent = companies.get(row.dependent_company_id)
        if hub is None or dependent is None:
            continue

        edges.append(
            ExposureEdge(
                hub=hub.ticker, dependent=dependent.ticker, mention_count=row.mention_count
            )
        )
        reach[hub.ticker] = reach.get(hub.ticker, 0) + 1
        upstream[dependent.ticker] = upstream.get(dependent.ticker, 0) + 1
        involved.add(hub.ticker)
        involved.add(dependent.ticker)

    nodes = [
        ExposureNode(
            ticker=c.ticker,
            name=c.name,
            sector=c.sector,
            reach=reach.get(c.ticker, 0),
            upstream=upstream.get(c.ticker, 0),
        )
        for c in companies.values()
        if c.ticker in involved
    ]
    nodes.sort(key=lambda n: -n.reach)
    return ExposureGraph(nodes=nodes, edges=edges)
