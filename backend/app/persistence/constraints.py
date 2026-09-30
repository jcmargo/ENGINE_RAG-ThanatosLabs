"""Nombres de restricción compartidos entre modelos ORM y migraciones."""

UQ_TENANTS_SLUG = "uq_tenants_slug"
CK_TENANTS_STATUS = "ck_tenants_status"

UQ_USERS_EMAIL = "uq_users_email"
CK_USERS_STATUS = "ck_users_status"
CK_USERS_SYSTEM_ROLE = "ck_users_system_role"

UQ_MEMBERSHIPS_TENANT_USER = "uq_memberships_tenant_user"
CK_MEMBERSHIPS_ROLE = "ck_memberships_role"
CK_MEMBERSHIPS_STATUS = "ck_memberships_status"
FK_MEMBERSHIPS_TENANT_ID = "fk_memberships_tenant_id"
FK_MEMBERSHIPS_USER_ID = "fk_memberships_user_id"
IX_MEMBERSHIPS_USER_ID = "ix_memberships_user_id"

UQ_ACTIVATION_TOKENS_HASH = "uq_activation_tokens_token_hash"
CK_ACTIVATION_TOKENS_STATUS = "ck_activation_tokens_status"
FK_ACTIVATION_TOKENS_TENANT_ID = "fk_activation_tokens_tenant_id"
FK_ACTIVATION_TOKENS_USER_ID = "fk_activation_tokens_user_id"
IX_ACTIVATION_TOKENS_TENANT_USER = "ix_activation_tokens_tenant_user"

UQ_AUTH_SESSIONS_REFRESH_HASH = "uq_auth_sessions_refresh_hash"
CK_AUTH_SESSIONS_STATUS = "ck_auth_sessions_status"
FK_AUTH_SESSIONS_USER_ID = "fk_auth_sessions_user_id"
FK_AUTH_SESSIONS_TENANT_ID = "fk_auth_sessions_tenant_id"
FK_AUTH_SESSIONS_REPLACED_BY = "fk_auth_sessions_replaced_by"
IX_AUTH_SESSIONS_USER_ID = "ix_auth_sessions_user_id"

UQ_API_KEYS_PREFIX = "uq_api_keys_prefix"
CK_API_KEYS_STATUS = "ck_api_keys_status"
FK_API_KEYS_TENANT_ID = "fk_api_keys_tenant_id"
IX_API_KEYS_TENANT_ID = "ix_api_keys_tenant_id"
