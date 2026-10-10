-- 多资产版本：同一发布者允许在不同 asset_type 下使用相同资产名称。
-- 本变更只调整唯一索引，不新增或删除字段；脚本按版本仅执行一次。

ALTER TABLE `market_assets`
  DROP INDEX `uk_publisher_name`,
  ADD UNIQUE KEY `uk_publisher_asset_type_name` (`publisher_id`, `asset_type`, `name`);
