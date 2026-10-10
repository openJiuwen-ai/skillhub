// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import JSZip from 'jszip'

/** 仅含 EOCD 的空 zip 最少 22 字节。 */
const MIN_ZIP_BYTES = 22

export function isJszipCorruptMessage(message: string): boolean {
  return /Corrupted zip|Can't find end of central directory|End of data reached/i.test(message)
}

export async function zipEntryString(entry: JSZip.JSZipObject, corruptCode: string): Promise<string> {
  try {
    return await entry.async('string')
  } catch (e) {
    if (e instanceof Error && isJszipCorruptMessage(e.message)) throw new Error(corruptCode)
    throw e
  }
}

/** 只在打开 zip 这一层把 JSZip 失败映射为业务码；后续条目/业务错误原样上抛。 */
export async function loadPublishZip(file: File, corruptCode: string): Promise<JSZip> {
  if (!file.size || file.size < MIN_ZIP_BYTES) {
    throw new Error(corruptCode)
  }
  const buf = await file.arrayBuffer()
  if (buf.byteLength < MIN_ZIP_BYTES) {
    throw new Error(corruptCode)
  }
  try {
    return await JSZip.loadAsync(buf)
  } catch {
    throw new Error(corruptCode)
  }
}
