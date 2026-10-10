-- 增量 v0.0.2.B003：market_assets 增加资产可见性（openjiuwen_market）
-- 适用：已有 baseline 库、market_assets 尚未包含 visibility。
-- 回滚：../../../rollback/v0.0.2.B003/openjiuwen_market/DDL/market_assets_visibility.sql

ALTER TABLE `market_assets` ADD COLUMN `visibility` varchar(32) NOT NULL DEFAULT 'public' COMMENT 'Asset visibility: public | private' AFTER `public_latest_version`;
CREATE INDEX `idx_market_assets_visibility` ON `market_assets` (`visibility`);
