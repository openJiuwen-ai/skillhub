CREATE TABLE `site_notifications` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `inbox_key` varchar(192) NOT NULL,
  `template` varchar(32) NOT NULL,
  `created_at_ms` bigint NOT NULL,
  `read_at_ms` bigint DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `ix_site_notifications_created_at_ms` (`created_at_ms`),
  KEY `idx_inbox_created` (`inbox_key`,`created_at_ms`)
) ENGINE=InnoDB AUTO_INCREMENT=2550 DEFAULT CHARSET=utf8mb4;