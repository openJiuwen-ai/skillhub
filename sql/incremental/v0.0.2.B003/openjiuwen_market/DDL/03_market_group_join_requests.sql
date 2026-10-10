-- 增量 v0.0.2.B003：组群入组申请表（openjiuwen_market）
-- 依赖：01_market_groups.sql

CREATE TABLE IF NOT EXISTS `market_group_join_requests` (
  `request_id` varchar(64) NOT NULL,
  `group_id` varchar(64) NOT NULL,
  `user_id` varchar(64) NOT NULL,
  `user_name` varchar(128) DEFAULT NULL,
  `message` text,
  `status` varchar(32) NOT NULL DEFAULT 'pending',
  `operator_id` varchar(64) DEFAULT NULL,
  `operator_name` varchar(128) DEFAULT NULL,
  `create_time` bigint NOT NULL,
  `update_time` bigint NOT NULL,
  PRIMARY KEY (`request_id`),
  UNIQUE KEY `uk_group_join_user_status` (`group_id`,`user_id`,`status`),
  KEY `idx_group_join_group_id` (`group_id`),
  KEY `idx_group_join_user_id` (`user_id`),
  KEY `idx_group_join_status` (`status`),
  CONSTRAINT `fk_group_join_group_id` FOREIGN KEY (`group_id`) REFERENCES `market_groups` (`group_id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
