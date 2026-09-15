"""028 projects 增加 taiji_username 列（把"用户"从字符串约定变成可查询的维度）

背景:
- 目标层级是 站点 → 用户 → 令牌。站点已落在 data_source_id 上(见迁移 027),
  令牌就是 projects 行本身,唯独中间的"用户"至今不是一个真实维度。
- 它现在只存在于 external_project_id 的字符串约定里("<用户名>:<令牌名>"),而且
  解析发生在浏览器端:accounts、metering、daily-report 三个页面各写了一份,且
  边界处理并不一致(一处判 idx <= 0,两处判 i < 0,对 ":tok" 这种输入会归到不同
  的用户下)。
- 后果是:用户维度不能在服务端筛选、不能聚合、令牌改名即断。前端只能把整份账号
  列表拉下来在浏览器里分组。

做法:
- 加一列 taiji_username,并从既有的 external_project_id 回填。纯字符串操作,
  不触碰任何费用数字,可回滚。
- external_project_id 保持原样不动 —— 它仍是 billing_summary.project_id 的连接
  键(见 uix_billing_dedup 与各处 join),改它会波及全部历史账单行。这一列是
  额外的、冗余的读取维度,不是替代。

回填的边界:
- 只回填 provider = 'taiji' 的货源下的项目;其它云没有这个概念。
- 要求冒号前非空(position(':' ...) > 1)。没有冒号、或形如 ":令牌" 的行留
  NULL —— 那正是"用户名未知",与前端今天把它们归成"未知用户"一致,而不是
  硬造一个空字符串用户。

向后兼容:
- 可空列,新增,不影响任何既有查询。
- downgrade 直接删列;由于没有任何写入方以外的东西依赖它,删掉是安全的。

Revision ID: p2j3k4l5
Revises: o1i2j3k4
Create Date: 2026-09-15
"""

from alembic import op
import sqlalchemy as sa


revision = "p2j3k4l5"
down_revision = "o1i2j3k4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("taiji_username", sa.String(200), nullable=True))
    op.create_index("ix_projects_taiji_username", "projects", ["taiji_username"])

    # 回填：取第一个冒号之前的部分，与前端现有解析一致。
    op.execute(
        """
        UPDATE projects p
        SET taiji_username = split_part(p.external_project_id, ':', 1)
        FROM supply_sources ss
        WHERE ss.id = p.supply_source_id
          AND ss.provider = 'taiji'
          AND p.taiji_username IS NULL
          AND position(':' in p.external_project_id) > 1
        """
    )


def downgrade() -> None:
    op.drop_index("ix_projects_taiji_username", table_name="projects")
    op.drop_column("projects", "taiji_username")
