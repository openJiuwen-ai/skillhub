// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

/** 与 marketplace `APP_VERSION_PATTERN` / jiuwenswarm `normalize_app_version` 对齐。 */
export const APP_VERSION_PATTERN = /^\d+(?:\.\d+){0,3}(?:[-+][0-9A-Za-z.-]{1,16})?$/
export const APP_VERSION_MAX_LEN = 32

/** 应用版本以包内 plugin.yaml 为准：只去首尾空白，不做 semver 归一化改写。 */
export function normalizeAppVersion(raw: string): string {
  return raw.trim()
}

export function isValidAppVersion(raw: string): boolean {
  const v = raw.trim()
  if (!v || v.length > APP_VERSION_MAX_LEN) return false
  return APP_VERSION_PATTERN.test(v)
}
