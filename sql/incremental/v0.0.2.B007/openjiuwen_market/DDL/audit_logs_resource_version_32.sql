-- 增量 v0.0.2.B007：审计日志资源版本列与市场版本上限对齐（openjiuwen_market）
-- 适用：已有库 audit_logs.resource_version 仍为 varchar(30)；应用版本最长 32 字符，
--       发布/下载审计写入 31-32 字符版本时旧列会截断或报错。
-- 执行前请备份。重复执行 MODIFY 为同一类型是安全的。
-- 回滚：../../../rollback/v0.0.2.B007/openjiuwen_market/DDL/audit_logs_resource_version_30.sql

ALTER TABLE `audit_logs`
  MODIFY COLUMN `resource_version` varchar(32) DEFAULT NULL COMMENT '资源版本；与市场/应用版本上限 32 字符对齐';
