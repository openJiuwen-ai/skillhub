-- 回滚前必须先处理同一 publisher_id + name 下跨 asset_type 的重名数据，
-- 否则恢复旧唯一索引会失败。

ALTER TABLE `market_assets`
  DROP INDEX `uk_publisher_asset_type_name`,
  ADD UNIQUE KEY `uk_publisher_name` (`publisher_id`, `name`);
