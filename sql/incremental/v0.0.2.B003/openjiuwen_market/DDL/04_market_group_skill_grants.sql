-- 增量 v0.0.2.B003：组群 Skill 授权表（openjiuwen_market）
-- 依赖：01_market_groups.sql、既有 market_assets 表

CREATE TABLE IF NOT EXISTS `market_group_skill_grants` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `group_id` varchar(64) NOT NULL,
  `asset_id` varchar(64) NOT NULL,
  `status` varchar(32) NOT NULL DEFAULT 'pending',
  `operator_id` varchar(64) DEFAULT NULL,
  `operator_name` varchar(128) DEFAULT NULL,
  `create_time` bigint NOT NULL,
  `update_time` bigint NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_group_skill_grant` (`group_id`,`asset_id`),
  KEY `idx_group_skill_grants_group_id` (`group_id`),
  KEY `idx_group_skill_grants_asset_id` (`asset_id`),
  KEY `idx_group_skill_grants_status` (`status`),
  CONSTRAINT `fk_group_skill_grants_group_id` FOREIGN KEY (`group_id`) REFERENCES `market_groups` (`group_id`) ON DELETE CASCADE,
  CONSTRAINT `fk_group_skill_grants_asset_id` FOREIGN KEY (`asset_id`) REFERENCES `market_assets` (`asset_id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
