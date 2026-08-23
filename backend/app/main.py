from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import app.models  # noqa: F401  (registers all models before any relationship resolution)
from app.api.routes import companies, documents, watchlists

app = FastAPI(title="Loom API", version="0.1.0")

# Single-user MVP, local frontend dev server only.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(companies.router)
app.include_router(watchlists.router)
app.include_router(documents.router)


@app.get("/health")
def health():
    return {"status": "ok"}
