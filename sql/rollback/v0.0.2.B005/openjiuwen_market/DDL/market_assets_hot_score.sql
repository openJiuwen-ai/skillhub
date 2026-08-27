-- 回滚 v0.0.2.B005：market_assets.hot_score（openjiuwen_market）

ALTER TABLE `market_assets` DROP INDEX `idx_hot_score`;
ALTER TABLE `market_assets` DROP COLUMN `hot_score`;
