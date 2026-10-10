-- 增量 v0.0.2.B003：组群成员表（openjiuwen_market）
-- 依赖：01_market_groups.sql

CREATE TABLE IF NOT EXISTS `market_group_members` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `group_id` varchar(64) NOT NULL,
  `user_id` varchar(64) NOT NULL,
  `user_name` varchar(128) DEFAULT NULL,
  `role` varchar(32) NOT NULL DEFAULT 'member',
  `create_time` bigint NOT NULL,
  `update_time` bigint NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_group_member_user` (`group_id`,`user_id`),
  KEY `idx_group_members_group_id` (`group_id`),
  KEY `idx_group_members_user_id` (`user_id`),
  CONSTRAINT `fk_group_members_group_id` FOREIGN KEY (`group_id`) REFERENCES `market_groups` (`group_id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
