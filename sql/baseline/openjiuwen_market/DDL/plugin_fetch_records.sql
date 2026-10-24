CREATE TABLE `plugin_fetch_records` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `asset_id` varchar(64) NOT NULL,
  `version_id` varchar(64) NOT NULL,
  `fetch_user_id` varchar(64) DEFAULT NULL,
  `create_time` bigint NOT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_asset_id` (`asset_id`),
  KEY `idx_fetch_user_id` (`fetch_user_id`)
) ENGINE=InnoDB AUTO_INCREMENT=44725 DEFAULT CHARSET=utf8mb4;