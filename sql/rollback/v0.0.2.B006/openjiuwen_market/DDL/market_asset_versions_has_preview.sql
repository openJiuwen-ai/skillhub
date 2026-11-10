-- 回滚 v0.0.2.B006：移除 market_asset_versions.has_preview

ALTER TABLE `market_asset_versions` DROP COLUMN `has_preview`;
