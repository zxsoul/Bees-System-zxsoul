"""daily_reports 增加「确认后入库」字段

日报流程从「生成即入库」改为「先生成草稿，员工确认后才入库」：

- ``confirmed``：是否已确认入库（草稿=False，确认后=True）。
- ``confirmed_at``：确认时间。

存量数据处理：迁移前已生成的 completed 日报视为已入库（confirmed=True），
避免历史日报在前端突然变成「待确认草稿」。

Revision ID: 003
Revises: 002
Create Date: 2026-10-12
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "daily_reports",
        sa.Column("confirmed", sa.Boolean(), nullable=True),
    )
    op.add_column(
        "daily_reports",
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
    )
    # 存量日报回填：completed 视为已确认入库，其余（generating/failed）为草稿
    op.execute("UPDATE daily_reports SET confirmed = (status = 'completed')")
    op.alter_column(
        "daily_reports",
        "confirmed",
        nullable=False,
        server_default=sa.text("false"),
    )


def downgrade() -> None:
    op.drop_column("daily_reports", "confirmed_at")
    op.drop_column("daily_reports", "confirmed")
