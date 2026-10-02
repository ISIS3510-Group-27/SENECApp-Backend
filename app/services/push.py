"""Push delivery through Firebase Cloud Messaging (FCM).

FCM needs a Firebase service-account key (``FIREBASE_CREDENTIALS_PATH``). Without
one, ``PUSH_PROVIDER=none`` keeps notifications in-app only, which is enough for
local development and for the analytics.
"""

import logging
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Protocol

from app.core.config import get_settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PushMessage:
    token: str
    title: str
    body: str
    data: dict[str, str] = field(default_factory=dict)


@dataclass
class PushResult:
    delivered_tokens: set[str] = field(default_factory=set)
    # Tokens FCM reported as no longer valid; they should be deleted.
    invalid_tokens: set[str] = field(default_factory=set)


class PushSender(Protocol):
    enabled: bool

    def send(self, messages: list[PushMessage]) -> PushResult: ...


class DisabledPushSender:
    enabled = False

    def send(self, messages: list[PushMessage]) -> PushResult:
        return PushResult()


class FcmPushSender:
    enabled = True
    BATCH_SIZE = 500  # FCM limit for send_each

    def __init__(self, credentials_path: str) -> None:
        import firebase_admin
        from firebase_admin import credentials

        try:
            self._app = firebase_admin.get_app("senecapp-push")
        except ValueError:
            self._app = firebase_admin.initialize_app(
                credentials.Certificate(credentials_path), name="senecapp-push"
            )

    def send(self, messages: list[PushMessage]) -> PushResult:
        from firebase_admin import exceptions, messaging

        result = PushResult()
        for start in range(0, len(messages), self.BATCH_SIZE):
            batch = messages[start : start + self.BATCH_SIZE]
            fcm_messages = [
                messaging.Message(
                    token=m.token,
                    notification=messaging.Notification(title=m.title, body=m.body),
                    data=m.data,
                )
                for m in batch
            ]
            try:
                response = messaging.send_each(fcm_messages, app=self._app)
            except exceptions.FirebaseError:
                logger.exception("FCM batch failed")
                continue
            for message, outcome in zip(batch, response.responses, strict=True):
                if outcome.success:
                    result.delivered_tokens.add(message.token)
                elif isinstance(
                    outcome.exception, messaging.UnregisteredError | messaging.SenderIdMismatchError
                ):
                    result.invalid_tokens.add(message.token)
        return result


@lru_cache
def get_push_sender() -> PushSender:
    settings = get_settings()
    if settings.push_provider == "fcm":
        return FcmPushSender(settings.firebase_credentials_path)
    return DisabledPushSender()
