"""Sign in, sign out, and who am I.

Two endpoints do the work. Requesting a code answers identically whether or not
the address is known, because anything else turns this into a way to find out
who uses Loom. Verifying a code returns a session token once; it is stored only
as a digest, so the response body is the only moment it exists in readable form.
"""

import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from pydantic import BaseModel, Field

from app.api.deps import DbSession
from app.config import settings
from app.models.account import User
from app.services import auth as auth_service
from app.services import usernames
from app.services.mailer import send_login_code, smtp_configured

logger = logging.getLogger(__name__)
router = APIRouter(tags=["auth"], prefix="/auth")

# Hosts that catch mail instead of forwarding it. Recognised so the app can
# point somebody at the right place to read their code rather than at an inbox
# the message will never reach.
_LOCAL_MAIL_HOSTS = frozenset({"mail", "localhost", "127.0.0.1", "mailpit", "mailhog"})


class SignUpRequest(BaseModel):
    email: str = Field(max_length=254)
    username: str = Field(max_length=64)
    password: str = Field(max_length=256)


class SignInRequest(BaseModel):
    # Either an email address or a username. Which one is decided by whether
    # it contains an "@", which is safe because usernames cannot.
    identifier: str = Field(max_length=254)
    password: str = Field(max_length=256)


class SignUpResponse(BaseModel):
    # Identical whether the address was free or already taken, so this endpoint
    # cannot be used to discover who has an account.
    email: str
    delivery: str
    message: str


class CodeRequest(BaseModel):
    email: str = Field(max_length=254)


class CodeResponse(BaseModel):
    # Identical for a known and an unknown address. The only thing that varies
    # is where the code went, which is a property of this install rather than
    # of the account.
    sent: bool
    delivery: str
    message: str


class VerifyRequest(BaseModel):
    email: str = Field(max_length=254)
    code: str = Field(max_length=12)


class SessionResponse(BaseModel):
    token: str
    email: str
    username: str
    display_name: str | None = None


class UsernameCheck(BaseModel):
    username: str
    available: bool
    # Null when available. A sign-up form that rejects a name without saying
    # why makes people guess.
    problem: str | None = None


class MeResponse(BaseModel):
    email: str
    username: str
    display_name: str | None = None
    # Oversight only. Every account can run the engine; this says whether the
    # instance's own internals and other accounts are visible.
    is_admin: bool = False
    created_at: str | None = None
    verified: bool = True


def current_user(
    db: DbSession,
    authorization: str | None = Header(default=None),
) -> User:
    """The signed-in user, or 401.

    Bearer token in the Authorization header rather than a cookie, because the
    frontend is a separate origin from the API and a cookie would need CORS
    credentials and a same-site policy to match. A header is simpler and the
    token is not long-lived enough in a page's memory to be worth the ceremony.
    """
    token = ""
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()

    user = auth_service.user_for_token(db, token)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sign in to use this.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def optional_user(
    db: DbSession,
    authorization: str | None = Header(default=None),
) -> User | None:
    """The signed-in user if there is one, without demanding it.

    Lets a page serve the same content to everybody and personalise only the
    parts that depend on who is asking, instead of gating the whole product
    behind an account.
    """
    token = ""
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    return auth_service.user_for_token(db, token)


def admin_user(
    db: DbSession,
    authorization: str | None = Header(default=None),
) -> User:
    """A signed-in user with oversight, or 403.

    403 rather than 404 on the routes that use it. Hiding the existence of an
    admin surface would be security through obscurity on an endpoint whose
    presence is obvious from the client bundle anyway, and a 404 makes a
    permissions problem look like a broken deployment.
    """
    user = current_user(db, authorization)
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="That needs an admin account.",
        )
    return user


CurrentUser = Depends(current_user)
OptionalUser = Depends(optional_user)
AdminUser = Depends(admin_user)


def _deliver(email: str, code: str) -> tuple[str, str]:
    """Send a code and describe where it went, in words a user can act on."""
    if send_login_code(email, code, minutes=auth_service.CODE_TTL_MINUTES):
        if settings.smtp_host in _LOCAL_MAIL_HOSTS:
            # The bundled catcher holds mail rather than forwarding it, so
            # telling somebody to check their inbox would send them somewhere
            # the message will never arrive.
            return "inbox", (
                f"Code sent to {email}. This Loom install delivers to its own "
                f"mailbox: open http://localhost:8025 to read it. It expires in "
                f"{auth_service.CODE_TTL_MINUTES} minutes."
            )
        return "email", f"We sent a code to {email}. It expires in {auth_service.CODE_TTL_MINUTES} minutes."
    return "log", (
        "No email server is configured on this Loom install, so your code was "
        "written to the server log. Run `docker compose logs backend | tail -20` "
        "to read it."
    )


@router.post("/signup", response_model=SignUpResponse)
def sign_up(payload: SignUpRequest, db: DbSession):
    """Create an account and send a code to prove the address is real."""
    email = auth_service.normalise_email(payload.email)
    try:
        user, _ = auth_service.register(db, email, payload.username, payload.password)
    except auth_service.RegistrationError as exc:
        if str(exc) == "account_exists":
            # The same shape of answer a fresh sign-up gets. Somebody probing
            # addresses learns nothing; the real owner is told to sign in by
            # the email they receive, or by trying.
            db.commit()
            return SignUpResponse(
                email=email, delivery="email",
                message=(
                    "Check your email. If you already have a Loom account with "
                    "this address, sign in instead."
                ),
            )
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    code = auth_service.issue_code(db, user)
    db.commit()
    delivery, message = _deliver(email, code)
    return SignUpResponse(email=email, delivery=delivery, message=message)


@router.get("/username-available", response_model=UsernameCheck)
def username_available(username: str, db: DbSession):
    """Whether a username can be claimed, and why not if it cannot.

    Unlike an address, a username is public by construction, so saying it is
    taken discloses nothing. The alternative is a form that refuses names
    without telling anybody which are free.
    """
    problem = usernames.problem(username)
    if problem:
        return UsernameCheck(username=username, available=False, problem=problem)
    if auth_service.username_taken(db, username):
        return UsernameCheck(
            username=username, available=False, problem="That username is taken.",
        )
    return UsernameCheck(username=username, available=True)


@router.post("/signin", response_model=SessionResponse)
def sign_in(payload: SignInRequest, request: Request, db: DbSession):
    try:
        token = auth_service.sign_in(
            db, payload.identifier, payload.password,
            user_agent=request.headers.get("user-agent"),
        )
    except auth_service.SignInError as exc:
        db.commit()
        if str(exc) == "unverified":
            # 403 rather than 401, so the client can tell "prove your address"
            # apart from "wrong password" and show the code screen instead of
            # an error the user cannot act on.
            raise HTTPException(
                status_code=403,
                detail="Confirm your email address first. We can send you a new code.",
            ) from exc
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    user = auth_service.find_by_identifier(db, payload.identifier)
    db.commit()
    if user is None:
        # Unreachable: sign_in already resolved this account. Guarded rather
        # than asserted so a future refactor cannot turn it into a 500.
        raise HTTPException(status_code=401, detail="That email or password is not right.")
    return SessionResponse(
        token=token, email=user.email, username=user.username,
        display_name=user.display_name,
    )


@router.post("/request-code", response_model=CodeResponse)
def request_code(payload: CodeRequest, db: DbSession):
    email = auth_service.normalise_email(payload.email)
    if not auth_service.looks_like_email(email):
        raise HTTPException(status_code=400, detail="That does not look like an email address.")

    user = db.execute(
        select(User).where(User.email == email)
    ).scalars().first()
    if user is None:
        # Answered as though it worked. A "no such account" here would let
        # anybody test which addresses are registered.
        return CodeResponse(
            sent=True, delivery="email" if smtp_configured() else "log",
            message="If that address has a Loom account, a code is on its way.",
        )

    if auth_service.recently_requested(db, user):
        # Answered rather than refused, so this cannot be used to discover
        # whether an address has recently signed in.
        db.commit()
        return CodeResponse(
            sent=True,
            delivery="email" if smtp_configured() else "log",
            message="A code was just sent. Check again in a moment before requesting another.",
        )

    code = auth_service.issue_code(db, user)
    db.commit()
    delivery, message = _deliver(email, code)
    return CodeResponse(sent=True, delivery=delivery, message=message)


@router.post("/verify", response_model=SessionResponse)
def verify(payload: VerifyRequest, request: Request, db: DbSession):
    try:
        token = auth_service.verify_code(
            db, payload.email, payload.code,
            user_agent=request.headers.get("user-agent"),
        )
    except auth_service.VerificationError as exc:
        db.commit()   # attempt counters survive a failed verification
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    email = auth_service.normalise_email(payload.email)
    user = auth_service.find_user(db, email)
    db.commit()
    if user is None:
        raise HTTPException(status_code=400, detail="That code is not valid. Request a new one.")
    return SessionResponse(
        token=token, email=user.email, username=user.username,
        display_name=user.display_name,
    )


@router.post("/logout")
def logout(db: DbSession, authorization: str | None = Header(default=None)):
    token = ""
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    auth_service.revoke_token(db, token)
    db.commit()
    return {"ok": True}


@router.get("/me", response_model=MeResponse)
def me(user: User = CurrentUser):
    return MeResponse(
        email=user.email,
        username=user.username,
        is_admin=user.is_admin,
        display_name=user.display_name,
        created_at=user.created_at.isoformat() if user.created_at else None,
        verified=user.email_verified_at is not None,
    )
