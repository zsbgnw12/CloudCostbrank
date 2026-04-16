"""013 Casdoor 接入:users / 数据范围授权 / 模块开关 / ApiKey / Refresh 会话,并扩展 operation_logs

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "a7b8c9d0e1f2"
down_revision = "f6a7b8c9d0e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------- users -------
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("casdoor_sub", sa.String(length=128), nullable=False),
        sa.Column("username", sa.String(length=100), nullable=False),
        sa.Column("email", sa.String(length=200), nullable=True),
        sa.Column("display_name", sa.String(length=200), nullable=True),
        sa.Column("avatar_url", sa.String(length=500), nullable=True),
        sa.Column("roles", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_login_ip", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("casdoor_sub", name="uq_users_casdoor_sub"),
    )
    op.create_index("ix_users_casdoor_sub", "users", ["casdoor_sub"])
    op.create_index("ix_users_username", "users", ["username"])
    op.create_index("ix_users_email", "users", ["email"])

    # ------- user_cloud_account_grants -------
    op.create_table(
        "user_cloud_account_grants",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("cloud_account_id", sa.Integer(),
                  sa.ForeignKey("cloud_accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("scope", sa.String(length=10), server_default="READ", nullable=False),
        sa.Column("granted_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("granted_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", "cloud_account_id", name="uq_user_cloud_account"),
    )
    op.create_index("ix_ucag_user", "user_cloud_account_grants", ["user_id"])
    op.create_index("ix_ucag_cloud_account", "user_cloud_account_grants", ["cloud_account_id"])

    # ------- user_project_grants -------
    op.create_table(
        "user_project_grants",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.Integer(),
                  sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("scope", sa.String(length=10), server_default="READ", nullable=False),
        sa.Column("granted_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("granted_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", "project_id", name="uq_user_project"),
    )
    op.create_index("ix_upg_user", "user_project_grants", ["user_id"])
    op.create_index("ix_upg_project", "user_project_grants", ["project_id"])

    # ------- api_module_permissions -------
    op.create_table(
        "api_module_permissions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("module", sa.String(length=50), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("description", sa.String(length=200), nullable=True),
        sa.Column("updated_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("module", name="uq_api_module_permissions_module"),
    )
    op.create_index("ix_amp_module", "api_module_permissions", ["module"])

    # Seed default modules — all enabled by default; admins can toggle later.
    op.execute("""
        INSERT INTO api_module_permissions (module, enabled, description) VALUES
          ('cloud_accounts',   true, '云账号管理'),
          ('bills',            true, '账单(月度/明细)'),
          ('billing',          true, '计费明细数据'),
          ('resources',        true, '资源盘点'),
          ('metering',         true, '用量计量'),
          ('sync',             true, '同步触发与日志'),
          ('data_sources',     true, '数据源管理'),
          ('alerts',           true, '告警规则'),
          ('suppliers',        true, '供应商'),
          ('exchange_rates',   true, '汇率'),
          ('dashboard',        true, '仪表盘聚合'),
          ('service_accounts', true, '服务账号'),
          ('azure_deploy',     true, 'Azure 模型部署'),
          ('azure_consent',    true, 'Azure 多租户授权'),
          ('categories',       true, '分类字典'),
          ('projects',         true, '项目管理')
        ON CONFLICT (module) DO NOTHING
    """)

    # ------- api_keys -------
    op.create_table(
        "api_keys",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("key_prefix", sa.String(length=16), nullable=False),
        sa.Column("owner_user_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("allowed_modules", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("allowed_cloud_account_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("key_hash", name="uq_api_keys_key_hash"),
    )
    op.create_index("ix_api_keys_hash", "api_keys", ["key_hash"])
    op.create_index("ix_api_keys_owner", "api_keys", ["owner_user_id"])

    # ------- auth_refresh_sessions -------
    op.create_table(
        "auth_refresh_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("jti", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("user_agent", sa.String(length=500), nullable=True),
        sa.Column("ip", sa.String(length=64), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("jti", name="uq_auth_refresh_sessions_jti"),
    )
    op.create_index("ix_ars_jti", "auth_refresh_sessions", ["jti"])
    op.create_index("ix_ars_user", "auth_refresh_sessions", ["user_id"])

    # ------- operation_logs: 扩展字段 -------
    # 原 operator 是 String(50),这里放宽到 String(100) 以容纳更长的标签
    op.alter_column("operation_logs", "operator", type_=sa.String(length=100), existing_nullable=True)
    op.add_column("operation_logs",
                  sa.Column("user_id", sa.Integer(),
                            sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True))
    op.add_column("operation_logs", sa.Column("casdoor_sub", sa.String(length=128), nullable=True))
    op.add_column("operation_logs", sa.Column("auth_method", sa.String(length=20), nullable=True))
    op.add_column("operation_logs", sa.Column("ip", sa.String(length=64), nullable=True))
    op.add_column("operation_logs", sa.Column("trace_id", sa.String(length=64), nullable=True))
    op.create_index("ix_operation_logs_user", "operation_logs", ["user_id"])
    op.create_index("ix_operation_logs_casdoor_sub", "operation_logs", ["casdoor_sub"])
    op.create_index("ix_operation_logs_trace", "operation_logs", ["trace_id"])


def downgrade() -> None:
    op.drop_index("ix_operation_logs_trace", table_name="operation_logs")
    op.drop_index("ix_operation_logs_casdoor_sub", table_name="operation_logs")
    op.drop_index("ix_operation_logs_user", table_name="operation_logs")
    op.drop_column("operation_logs", "trace_id")
    op.drop_column("operation_logs", "ip")
    op.drop_column("operation_logs", "auth_method")
    op.drop_column("operation_logs", "casdoor_sub")
    op.drop_column("operation_logs", "user_id")
    op.alter_column("operation_logs", "operator", type_=sa.String(length=50), existing_nullable=True)

    op.drop_index("ix_ars_user", table_name="auth_refresh_sessions")
    op.drop_index("ix_ars_jti", table_name="auth_refresh_sessions")
    op.drop_table("auth_refresh_sessions")

    op.drop_index("ix_api_keys_owner", table_name="api_keys")
    op.drop_index("ix_api_keys_hash", table_name="api_keys")
    op.drop_table("api_keys")

    op.drop_index("ix_amp_module", table_name="api_module_permissions")
    op.drop_table("api_module_permissions")

    op.drop_index("ix_upg_project", table_name="user_project_grants")
    op.drop_index("ix_upg_user", table_name="user_project_grants")
    op.drop_table("user_project_grants")

    op.drop_index("ix_ucag_cloud_account", table_name="user_cloud_account_grants")
    op.drop_index("ix_ucag_user", table_name="user_cloud_account_grants")
    op.drop_table("user_cloud_account_grants")

    op.drop_index("ix_users_email", table_name="users")
    op.drop_index("ix_users_username", table_name="users")
    op.drop_index("ix_users_casdoor_sub", table_name="users")
    op.drop_table("users")
