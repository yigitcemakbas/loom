"""A durable credential for programmatic access, stored as a digest.

Loom's only credential until now was a browser session: issued by emailing a
six-digit code, valid ninety days. That is right for a person signing in most
mornings and useless for an agent, which has no inbox and needs a secret it can
hold in configuration. The evidence API exists because the reader benchmark
found agents reading evidence were the best-performing arm, and there was no way
to give one a credential.

**Read-only by construction, not by a permission check.** An API key reaches the
evidence surface and nothing else, because the dependency that accepts keys is
wired only to read routes while every mutating route keeps the session-only
dependency. The blast radius of a leaked key matters more here than elsewhere:
adding a ticker queues a full filing-history ingest, so a write-capable key is a
way for a stranger to spend an instance's SEC rate limit and model quota.

Stored as a sha256 digest, like `user_sessions`, so the database never holds a
usable credential. The plaintext is shown once at creation and cannot be
recovered, which is the only honest way to do this: a key the server can read
back is a key an attacker can read back.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ApiKey(Base):
    """One programmatic credential belonging to one account."""

    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # What the key is for, in the owner's words. Present because the only way to
    # decide whether revoking a key is safe is to know what it was issued to,
    # and an opaque list of digests cannot answer that.
    name: Mapped[str] = mapped_column(String(80), nullable=False)

    # The leading characters of the plaintext, stored in clear. Enough to match
    # a key in a log or a config file against a row here, and far too little to
    # authenticate with.
    prefix: Mapped[str] = mapped_column(String(16), nullable=False, index=True)

    key_hash: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Written on use, so an owner can tell a live key from a forgotten one
    # before deciding to revoke it.
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Revoked rather than deleted: a key that turns up in a log afterwards
    # should be identifiable, which a deleted row cannot do.
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @property
    def active(self) -> bool:
        return self.revoked_at is None
