-- 增量 v0.0.2.B001：market_skill_reviews（openjiuwen_market）
-- 适用：已有 baseline 库、尚未包含 market_skill_reviews 表。
-- 执行前请备份。本脚本按版本仅执行一次；重复执行可能因表/索引已存在而报错。
-- 同版本另需执行：git_sources.sql、market_assets.sql、market_asset_versions.sql
-- 建议顺序：git_sources.sql -> market_assets.sql / market_asset_versions.sql -> market_skill_reviews.sql
-- 回滚：../../../rollback/v0.0.2.B001/openjiuwen_market/DDL/market_skill_reviews_rollback.sql

CREATE TABLE IF NOT EXISTS `market_skill_reviews` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `asset_id` varchar(64) NOT NULL,
  `version_id` varchar(64) NOT NULL,
  `review_status` varchar(32) NOT NULL DEFAULT 'queued',
  `review_failed_reason` text,
  `review_mode` varchar(64) DEFAULT NULL,
  `review_engine` varchar(128) DEFAULT NULL,
  `model_name` varchar(256) DEFAULT NULL,
  `policy_version` varchar(128) DEFAULT NULL,
  `score` int DEFAULT NULL,
  `overall` varchar(32) DEFAULT NULL,
  `risk` varchar(32) DEFAULT NULL,
  `conclusion` text,
  `metrics_json` json DEFAULT NULL,
  `sections_json` json DEFAULT NULL,
  `raw_output_json` json DEFAULT NULL,
  `trace_id` varchar(128) DEFAULT NULL,
  `started_at` bigint DEFAULT NULL,
  `finished_at` bigint DEFAULT NULL,
  `created_at` bigint NOT NULL,
  `updated_at` bigint NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_skill_review_asset_version` (`asset_id`,`version_id`),
  KEY `ix_market_skill_reviews_asset_id` (`asset_id`),
  KEY `ix_market_skill_reviews_version_id` (`version_id`),
  KEY `idx_skill_review_status` (`review_status`),
  CONSTRAINT `fk_market_skill_reviews_asset_id` FOREIGN KEY (`asset_id`) REFERENCES `market_assets` (`asset_id`),
  CONSTRAINT `fk_market_skill_reviews_version_id` FOREIGN KEY (`version_id`) REFERENCES `market_asset_versions` (`version_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
