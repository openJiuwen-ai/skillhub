-- 增量 v0.0.2.B003：Playground 每日使用配额跟踪表（openjiuwen_market）
-- 回滚：../../../rollback/v0.0.2.B003/openjiuwen_market/DDL/playground_usage_rollback.sql

CREATE TABLE `playground_usage` (
  `user_id`       varchar(128) NOT NULL COMMENT 'OAuth 用户 ID（来自 acting_user_id）',
  `usage_date`    date         NOT NULL COMMENT '日期（UTC 自然日，与代码 _today_utc/_next_midnight_utc 一致）',
  `session_count` int          NOT NULL DEFAULT '0' COMMENT '当日已创建 session 数',
  `updated_at`    bigint       NOT NULL COMMENT '最后更新时间（Unix 毫秒）',
  PRIMARY KEY (`user_id`, `usage_date`),
  KEY `idx_usage_date` (`usage_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
  COMMENT='Playground 每日使用配额跟踪；PLAYGROUND_DAILY_LIMIT 控制上限，0=不限制';
