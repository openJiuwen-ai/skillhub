-- 增量 v0.0.2.B005：market_assets 增加火爆值 hot_score（openjiuwen_market）
-- 适用：已有 baseline 库、market_assets 尚未包含 hot_score。
-- 回滚：../../../rollback/v0.0.2.B005/openjiuwen_market/DDL/market_assets_hot_score.sql

ALTER TABLE `market_assets` ADD COLUMN `hot_score` double NOT NULL DEFAULT 0.0 COMMENT '火爆值：近期下载+累计下载+浏览+互动+评分的加权对数综合分（离线定时重算）' AFTER `pin_order`;
CREATE INDEX `idx_hot_score` ON `market_assets` (`hot_score`);
