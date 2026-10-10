-- 回滚 v0.0.2.B003：market_assets.visibility（openjiuwen_market）

ALTER TABLE `market_assets` DROP INDEX `idx_market_assets_visibility`;
ALTER TABLE `market_assets` DROP COLUMN `visibility`;
