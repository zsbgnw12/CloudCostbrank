"""022 新增主体表 entities,在 supply_sources 和 projects 之间增加一层

层级：suppliers → supply_sources → entities → projects
- entities.supply_source_id 外键 ON DELETE CASCADE：货源删除连带清主体
- UNIQUE(supply_source_id, name)：同一货源下主体名唯一
- projects.entity_id 外键 ON DELETE SET NULL：主体删除不连带删服务账号，挂回未分配
"""

from alembic import op
import sqlalchemy as sa


revision = "j6d7e8f9"
down_revision = "i5c6d7e8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "entities",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "supply_source_id",
            sa.Integer(),
            sa.ForeignKey("supply_sources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("note", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("supply_source_id", "name", name="uq_entity_supply_src_name"),
    )
    op.create_index("ix_entities_supply_source_id", "entities", ["supply_source_id"])

    op.add_column(
        "projects",
        sa.Column("entity_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_projects_entity_id",
        "projects",
        "entities",
        ["entity_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_projects_entity_id", "projects", ["entity_id"])


def downgrade() -> None:
    op.drop_index("ix_projects_entity_id", table_name="projects")
    op.drop_constraint("fk_projects_entity_id", "projects", type_="foreignkey")
    op.drop_column("projects", "entity_id")
    op.drop_index("ix_entities_supply_source_id", table_name="entities")
    op.drop_table("entities")
