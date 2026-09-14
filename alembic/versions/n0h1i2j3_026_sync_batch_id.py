"""026 sync_logs 增加 batch_id(把"同一次派发"的多条日志归为一个任务)

背景:
- 一次"同步全部"(sync_all)会给每个数据源单独派发子任务,各产生一条 sync_logs。
- 之前无字段能把"同一次点击/同一次定时"的这批日志关联起来,前端只能平铺流水。
- 加 batch_id:派发时生成一个 run_id,写进这批每条日志 → 前端可按批次分组成"任务",
  点进去看这批各服务账号的成败。手动同步单个源 = 一个只含 1 行的批次。

向后兼容:可空列,历史行 batch_id 为 NULL(前端把"无批次号"的旧记录单独归类)。

Revision ID: n0h1i2j3
Revises: m9g0h1i2
Create Date: 2026-09-14
"""

from alembic import op
import sqlalchemy as sa


revision = "n0h1i2j3"
down_revision = "m9g0h1i2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sync_logs", sa.Column("batch_id", sa.String(40), nullable=True))
    op.create_index("ix_sync_logs_batch_id", "sync_logs", ["batch_id"])


def downgrade() -> None:
    op.drop_index("ix_sync_logs_batch_id", table_name="sync_logs")
    op.drop_column("sync_logs", "batch_id")
