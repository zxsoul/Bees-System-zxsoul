"""全员日报查看权限（can_view_team_reports）的鉴权测试。

覆盖 ``DailyReportService.require_team_reports_viewer`` 的四条路径：
- 被授权用户（flag=True）：跨空间（space_id=None）放行；
- 普通用户（flag=False）且非空间创建者：拒绝；
- 系统管理员：无条件放行；
- 空间创建者（主管）：指定空间时放行。
"""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.exceptions import ForbiddenException
from app.services.report_service import DailyReportService

ADMIN_EMAIL = "admin@example.com"


def _user(*, email: str = "user@example.com", flag: bool = False) -> MagicMock:
    user = MagicMock()
    user.id = uuid.uuid4()
    user.email = email
    user.can_view_team_reports = flag
    return user


def _service_with_space_creator(creator_id: uuid.UUID) -> DailyReportService:
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = creator_id
    db.execute.return_value = result
    return DailyReportService(db)


@pytest.fixture(autouse=True)
def _admin_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INITIAL_ADMIN_EMAIL", ADMIN_EMAIL)


async def test_flagged_user_can_view_across_spaces() -> None:
    service = DailyReportService(AsyncMock())
    # 跨空间（space_id=None）也放行，不应查询任何表
    await service.require_team_reports_viewer(_user(flag=True), None)


async def test_plain_user_denied_across_spaces() -> None:
    service = DailyReportService(AsyncMock())
    with pytest.raises(ForbiddenException):
        await service.require_team_reports_viewer(_user(flag=False), None)


async def test_admin_bypasses_without_flag() -> None:
    service = DailyReportService(AsyncMock())
    await service.require_team_reports_viewer(_user(email=ADMIN_EMAIL), None)


async def test_space_creator_passes_with_space() -> None:
    user = _user()
    service = _service_with_space_creator(user.id)
    await service.require_team_reports_viewer(user, uuid.uuid4())


async def test_non_creator_without_flag_denied_with_space() -> None:
    service = _service_with_space_creator(uuid.uuid4())  # 别人创建的空间
    with pytest.raises(ForbiddenException):
        await service.require_team_reports_viewer(_user(), uuid.uuid4())
