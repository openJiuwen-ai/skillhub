-- 增量 v0.0.2.B006：market_asset_versions 增加预览图标记 has_preview（openjiuwen_market）
-- 适用：已有 baseline 库、market_asset_versions 尚未包含 has_preview。
-- 执行前请备份。本脚本按版本仅执行一次；重复执行可能因列已存在而报错。
-- 回滚：../../../rollback/v0.0.2.B006/openjiuwen_market/DDL/market_asset_versions_has_preview.sql

ALTER TABLE `market_asset_versions`
  ADD COLUMN `has_preview` tinyint(1) NOT NULL DEFAULT '0' COMMENT '版本目录下是否存在 preview.png 预览图' AFTER `has_icon`;
