# Pinned to 3.12 deliberately: some dependencies ship compiled extensions that
# do not build on 3.13/3.14 yet. Containerising this is precisely what stops
# that being the user's problem.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Requirements copied first so the dependency layer is cached and a code change
# does not trigger a full reinstall.
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Document text lives here. Declared as a volume in compose so it survives
# container replacement.
RUN mkdir -p /app/data/blobs

EXPOSE 8000

# Migrations run on start rather than as a separate step the user has to know
# about. Idempotent: alembic no-ops when the schema is already current.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
