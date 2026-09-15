"""027 projects 唯一约束纳入 data_source_id(让同一货源下能按站点区分同名令牌)

背景:
- 计划在一个 taiji 货源下接入六个网关部署(站点),每个站点一个 CloudAccount +
  DataSource。账单表的去重键本来就含 data_source_id,所以存储层天然按站点隔离。
- 但 projects 的唯一约束是 (supply_source_id, external_project_id),不含
  data_source_id。而 taiji 的 external_project_id 是 "<用户名>:<令牌名>" —— 这个
  命名空间在互相独立的部署之间必然重复。于是站点 B 无法为自己的 alice:default
  建项目行:插入会撞约束。
- 更糟的是三处"项目是否已存在"的查重也不带 data_source_id(sync_service
  auto_create_taiji_projects、service_accounts 的 taiji_ingest_day 与
  taiji_from_blob),它们会认为站点 A 已建的行就是站点 B 要的,静默跳过。配合
  billing↔projects 的 join 收紧(见 fix/billing-project-join-data-source),
  站点 B 的账单行将匹配不到任何项目,费用从所有报表中静默消失。

为什么用两个部分唯一索引而不是一个三列约束:
- projects.data_source_id 可空,生产库 1314 行里有 126 行为 NULL。PostgreSQL 的
  唯一索引把 NULL 视为互不相等,若直接建三列约束,这 126 行将完全失去唯一性保护
  —— 可以插入任意多个同名项目。
- 这些 NULL 行来自 GCP 路径(auto_create_gcp_projects、discover_gcp_projects)
  和可选字段的手工创建;taiji 的三条创建路径都显式写入 data_source_id。因此按
  data_source_id 是否为 NULL 分成两个部分索引,既让有数据源的行按站点隔离,又让
  没有数据源的行保持与今天完全一致的约束强度。

向后兼容:
- 合并前的数据必然满足新索引 —— 旧约束比新索引更严格,不可能存在冲突行。
- 回滚会恢复原约束。若回滚时库里已存在"同货源同 external_project_id、但
  data_source_id 不同"的行(即已经接入了第二个站点),恢复旧约束会失败;这是预期
  行为,届时需要先决定保留哪一行,而不是让迁移悄悄合并它们。

Revision ID: o1i2j3k4
Revises: n0h1i2j3
Create Date: 2026-09-14
"""

from alembic import op
import sqlalchemy as sa


revision = "o1i2j3k4"
down_revision = "n0h1i2j3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("uq_project_supply_src_ext_id", "projects", type_="unique")

    # 有数据源的行:同一货源 + 同一数据源(= 站点)下 external_project_id 唯一
    op.create_index(
        "uq_project_ss_ds_ext_id",
        "projects",
        ["supply_source_id", "data_source_id", "external_project_id"],
        unique=True,
        postgresql_where=sa.text("data_source_id IS NOT NULL"),
    )

    # 没有数据源的行:维持旧语义,同一货源下 external_project_id 唯一
    op.create_index(
        "uq_project_ss_ext_id_no_ds",
        "projects",
        ["supply_source_id", "external_project_id"],
        unique=True,
        postgresql_where=sa.text("data_source_id IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_project_ss_ext_id_no_ds", table_name="projects")
    op.drop_index("uq_project_ss_ds_ext_id", table_name="projects")
    op.create_unique_constraint(
        "uq_project_supply_src_ext_id",
        "projects",
        ["supply_source_id", "external_project_id"],
    )
