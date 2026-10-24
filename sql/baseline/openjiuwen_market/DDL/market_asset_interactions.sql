CREATE TABLE `market_asset_interactions` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `asset_id` varchar(64) NOT NULL,
  `user_id` varchar(64) NOT NULL,
  `action_type` varchar(32) NOT NULL,
  `create_time` bigint DEFAULT NULL,
  `update_time` bigint DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_mai_asset_user_action` (`asset_id`,`user_id`,`action_type`),
  KEY `idx_mai_asset_id` (`asset_id`),
  KEY `idx_mai_user_id` (`user_id`),
  KEY `idx_mai_action_type` (`action_type`)
) ENGINE=InnoDB AUTO_INCREMENT=166 DEFAULT CHARSET=utf8mb4;