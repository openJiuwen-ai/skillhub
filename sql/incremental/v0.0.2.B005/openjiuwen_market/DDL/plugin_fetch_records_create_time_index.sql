-- 优化火爆值重算的近期下载聚合查询：按 create_time 过滤 + asset_id 分组
CREATE INDEX `idx_create_time_asset_id` ON `plugin_fetch_records` (`create_time`, `asset_id`);
