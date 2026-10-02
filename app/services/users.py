from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.auth.verifiers import VerifiedIdentity
from app.models import Interest, User, UserInterest
from app.schemas.user import UserUpdate


class UnknownInterestsError(Exception):
    def __init__(self, interest_ids: set[int]) -> None:
        super().__init__(f"Unknown interest ids: {sorted(interest_ids)}")
        self.interest_ids = interest_ids


def get_user(db: Session, user_id: int) -> User | None:
    """Load a user with interests (and their categories) eagerly."""
    stmt = (
        select(User)
        .where(User.id == user_id)
        .options(
            selectinload(User.interests)
            .selectinload(UserInterest.interest)
            .selectinload(Interest.category)
        )
    )
    return db.scalar(stmt)


def get_or_create_user(db: Session, identity: VerifiedIdentity) -> User:
    """Resolve the signed-in identity to a ``User``, creating it on first sign-in.

    Lookup order: Firebase UID, then email (links a pre-existing/seeded account to
    its Firebase identity), otherwise a new user is created.
    """
    user = db.scalar(select(User).where(User.firebase_uid == identity.uid))
    if user is None:
        user = db.scalar(select(User).where(func.lower(User.email) == identity.email))
        if user is None:
            user = User(
                email=identity.email,
                full_name=identity.name or identity.email.split("@")[0],
                avatar_url=identity.picture,
            )
            db.add(user)
        user.firebase_uid = identity.uid
        try:
            db.commit()
        except IntegrityError:
            # A concurrent first request created/linked the same account; use that one.
            db.rollback()
            user = db.scalars(select(User).where(User.firebase_uid == identity.uid)).one()
    return user


def update_profile(db: Session, user: User, changes: UserUpdate) -> None:
    for field, value in changes.model_dump(exclude_unset=True).items():
        setattr(user, field, str(value) if field == "avatar_url" and value else value)
    db.commit()


def replace_interests(db: Session, user: User, interest_ids: list[int]) -> None:
    """Set the user's interests to exactly ``interest_ids``.

    Interests kept from before retain their original opt-in timestamp, which the
    analytics rely on; only removed ones are deleted and only new ones inserted.
    """
    wanted = set(interest_ids)
    existing = set(db.scalars(select(Interest.id).where(Interest.id.in_(wanted))))
    if missing := wanted - existing:
        raise UnknownInterestsError(missing)

    current = {link.interest_id: link for link in user.interests}
    for interest_id in current.keys() - wanted:
        user.interests.remove(current[interest_id])
    for interest_id in wanted - current.keys():
        user.interests.append(UserInterest(interest_id=interest_id))
    db.commit()
