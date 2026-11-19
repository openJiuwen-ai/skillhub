-- 回滚 v0.0.2.B007：将 audit_logs.resource_version 恢复为 varchar(30)
-- 注意：若已存在长度大于 30 的历史版本数据，回滚会失败或截断，请先清理/缩短数据。

ALTER TABLE `audit_logs`
  MODIFY COLUMN `resource_version` varchar(30) DEFAULT NULL;
