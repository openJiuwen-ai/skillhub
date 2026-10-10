-- 增量 v0.0.2.B003：组群主表（openjiuwen_market）
-- 适用：已有 baseline 库、尚未包含组群管理表。
-- 执行前请备份。本脚本按版本仅执行一次；重复执行可能因表/索引已存在而报错。

CREATE TABLE IF NOT EXISTS `market_groups` (
  `group_id` varchar(64) NOT NULL,
  `name` varchar(128) NOT NULL,
  `description` text,
  `owner_id` varchar(64) NOT NULL,
  `owner_name` varchar(128) DEFAULT NULL,
  `visibility` varchar(32) NOT NULL DEFAULT 'private',
  `status` varchar(32) NOT NULL DEFAULT 'active',
  `member_count` int NOT NULL DEFAULT '0',
  `skill_count` int NOT NULL DEFAULT '0',
  `create_time` bigint NOT NULL,
  `update_time` bigint NOT NULL,
  PRIMARY KEY (`group_id`),
  KEY `idx_market_groups_owner_id` (`owner_id`),
  KEY `idx_market_groups_visibility` (`visibility`),
  KEY `idx_market_groups_status` (`status`),
  KEY `idx_market_groups_update_time` (`update_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
