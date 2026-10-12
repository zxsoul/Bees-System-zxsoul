"""users 增加「全员日报查看」功能级权限字段

进度中心 · 全员日报从「仅空间创建者/系统管理员可见」扩展为
「管理员可按用户授权」：

- ``can_view_team_reports``：True 时该用户可跨空间查看所有员工的
  日报（列表 + 详情 + 插图附件），False 时维持原有口径
  （仅本人日报；空间创建者/管理员不受影响）。

简单开关先行：后续若需要「按员工粒度授权查看」，再引入独立的
关联表，本字段作为全局快速开关保留。

Revision ID: 004
Revises: 003
Create Date: 2026-10-12
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "can_view_team_reports",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "can_view_team_reports")
