CREATE TABLE `market_asset_versions` (
  `version_id` varchar(64) NOT NULL,
  `asset_id` varchar(64) NOT NULL,
  `version` varchar(32) NOT NULL,
  `changelog` text,
  `status` varchar(32) DEFAULT NULL,
  `create_time` bigint DEFAULT NULL,
  `file_path` varchar(512) DEFAULT NULL,
  `artifact_sha256` varchar(64) DEFAULT NULL,
  `has_icon` tinyint(1) NOT NULL DEFAULT '0',
  `moderation_status` varchar(32) DEFAULT NULL,
  `moderation_reject_reason` text,
  PRIMARY KEY (`version_id`),
  UNIQUE KEY `uk_asset_version` (`asset_id`,`version`),
  KEY `ix_market_asset_versions_asset_id` (`asset_id`),
  CONSTRAINT `market_asset_versions_ibfk_1` FOREIGN KEY (`asset_id`) REFERENCES `market_assets` (`asset_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;