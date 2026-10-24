-- 增量 v0.0.2.B001：git_sources（openjiuwen_market）
-- 适用：已有 baseline 库、尚未包含 git_sources 表。
-- 执行前请备份。本脚本按版本仅执行一次；重复执行可能因表/索引已存在而报错。
-- 同版本另需执行：market_assets.sql
-- 回滚：../../../rollback/v0.0.2.B001/openjiuwen_market/DDL/git_sources_rollback.sql

CREATE TABLE IF NOT EXISTS `git_sources` (
  `id` varchar(64) NOT NULL,
  `name` varchar(128) NOT NULL,
  `repo_url` varchar(512) NOT NULL,
  `repo_url_canonical` varchar(640) DEFAULT NULL COMMENT 'normalized host+path for global dedup',
  `git_source_dedup_key` char(64) DEFAULT NULL COMMENT 'SHA256 hex: canonical + ref + skills_subpath',
  `ref` varchar(256) NOT NULL,
  `skills_subpath` varchar(512) DEFAULT NULL,
  `visibility_scope` varchar(32) NOT NULL,
  `created_by_user_id` varchar(64) NOT NULL,
  `create_time_ms` bigint NOT NULL,
  `update_time_ms` bigint NOT NULL,
  `last_index_status` varchar(64) DEFAULT NULL COMMENT 'syncing | success | partial_failure | failed',
  `last_index_error` text,
  `last_indexed_at_ms` bigint DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_git_source_dedup_key` (`git_source_dedup_key`),
  KEY `idx_gs_created_by` (`created_by_user_id`),
  KEY `idx_gs_visibility` (`visibility_scope`),
  KEY `idx_gs_repo_url_canonical` (`repo_url_canonical`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
