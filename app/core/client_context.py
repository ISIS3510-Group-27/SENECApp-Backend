from dataclasses import dataclass

from fastapi import Request

# Optional headers the mobile apps send on every request. They let server-side
# analytics events carry the same envelope as client-side ones.
SESSION_HEADER = "X-Session-Id"
APP_HEADER = "X-App"  # "flutter" | "kotlin"
APP_VERSION_HEADER = "X-App-Version"
PLATFORM_HEADER = "X-Platform"  # "android" | "ios"
DEVICE_MODEL_HEADER = "X-Device-Model"
OS_VERSION_HEADER = "X-OS-Version"


@dataclass(frozen=True)
class ClientContext:
    session_id: str | None = None
    app: str | None = None
    app_version: str | None = None
    platform: str | None = None
    device_model: str | None = None
    os_version: str | None = None


def get_client_context(request: Request) -> ClientContext:
    headers = request.headers

    def header(name: str, max_length: int) -> str | None:
        value = headers.get(name)
        if not value:
            return None
        return value.strip()[:max_length] or None

    return ClientContext(
        session_id=header(SESSION_HEADER, 64),
        app=header(APP_HEADER, 20),
        app_version=header(APP_VERSION_HEADER, 32),
        platform=header(PLATFORM_HEADER, 20),
        device_model=header(DEVICE_MODEL_HEADER, 100),
        os_version=header(OS_VERSION_HEADER, 50),
    )
