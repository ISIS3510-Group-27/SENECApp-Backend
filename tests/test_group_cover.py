import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import MembershipRole, StudentGroup
from tests.conftest import auth_header
from tests.factories import add_member, group_by_name, make_user

LEADER = auth_header("leader@uniandes.edu.co")
MEMBER = auth_header("member@uniandes.edu.co")


def _photo(fmt: str = "PNG", size: tuple[int, int] = (2400, 1200), gps: bool = False) -> bytes:
    image = Image.new("RGB", size, (200, 30, 30))
    out = io.BytesIO()
    if gps:
        exif = Image.Exif()
        exif[0x8825] = {2: (4.0, 36.0, 10.0)}  # GPSInfo: a latitude
        image.save(out, format="JPEG", exif=exif)
    else:
        image.save(out, format=fmt)
    return out.getvalue()


@pytest.fixture
def group(db_session: Session) -> StudentGroup:
    group = group_by_name(db_session, "Coro Uniandes")
    add_member(
        db_session, make_user(db_session, "leader@uniandes.edu.co"), group, MembershipRole.ADMIN
    )
    add_member(db_session, make_user(db_session, "member@uniandes.edu.co"), group)
    db_session.commit()
    return group


def _upload(client: TestClient, group: StudentGroup, data: bytes, headers: dict) -> object:
    return client.post(
        f"/api/v1/groups/{group.id}/cover",
        headers=headers,
        files={"file": ("cover.png", data, "image/png")},
    )


def _stored(url: str) -> Path:
    return Path(get_settings().media_root) / url.removeprefix("/media/")


def test_admin_uploads_a_cover_that_is_served(client: TestClient, group: StudentGroup) -> None:
    response = _upload(client, group, _photo(), LEADER)

    url = response.json()["image_url"]
    assert response.status_code == 200
    assert url.startswith(f"/media/groups/{group.id}/cover-")
    served = client.get(url)
    assert served.status_code == 200
    assert served.headers["content-type"] == "image/jpeg"
    image = Image.open(io.BytesIO(served.content))
    assert max(image.size) == 1600  # resized
    assert image.format == "JPEG"


def test_uploaded_photos_lose_their_gps_metadata(client: TestClient, group: StudentGroup) -> None:
    original = Image.open(io.BytesIO(_photo(gps=True)))
    assert original.getexif().get(0x8825)

    url = _upload(client, group, _photo(gps=True), LEADER).json()["image_url"]

    stored = Image.open(_stored(url))
    assert not stored.getexif()


def test_a_new_cover_replaces_the_old_file(client: TestClient, group: StudentGroup) -> None:
    first = _upload(client, group, _photo(), LEADER).json()["image_url"]
    second = _upload(client, group, _photo("JPEG"), LEADER).json()["image_url"]

    assert first != second
    assert not _stored(first).exists()
    assert _stored(second).exists()


def test_only_admins_upload_valid_images(client: TestClient, group: StudentGroup) -> None:
    assert _upload(client, group, _photo(), MEMBER).status_code == 403
    assert _upload(client, group, b"definitely not an image", LEADER).status_code == 422
    gif = io.BytesIO()
    Image.new("RGB", (10, 10)).save(gif, format="GIF")
    assert _upload(client, group, gif.getvalue(), LEADER).status_code == 422
    too_big = b"\xff\xd8\xff" + b"0" * (get_settings().max_upload_mb * 1024 * 1024 + 10)
    assert _upload(client, group, too_big, LEADER).status_code == 413
