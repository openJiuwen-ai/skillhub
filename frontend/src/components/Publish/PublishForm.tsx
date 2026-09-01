// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import { type FormEvent, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { CheckCircle2, CircleHelp, FolderUp, ImagePlus, Loader2, UploadCloud } from 'lucide-react'
import { createPortal } from 'react-dom'
import { useQuery } from 'react-query'
import type { PublishDrawerType } from '@/contexts/PublishDrawer'
import { getPlugins, getPublishTemplatePresigned, MarketplaceApiError, publishPlugin } from '@/api/plugin'
import { useGitCodeAuth } from '@/auth/GitCodeAuthContext'
import { sha256HexOfFile } from '@/utils/sha256File'
import {
  buildSkillPublishZip,
  detectPublishPluginType,
  normalizeSemver,
  parseSkillMdFrontmatter,
  type DetectedPublishPluginType,
} from '@/utils/buildSkillPublishZip'
import { inspectAgentPublishZip } from '@/utils/detectAgentPublishZip'
import { formatSkillVersionLabel } from '@/utils/formatSkillVersionLabel'
import { isAgentAssetPluginType } from '@/utils/pluginType'
import { resolvePublishFormFieldLabels } from '@/utils/publishFieldLabels'

const SKILL_NAME_PATTERN = /^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$/
const SKILL_NAME_MAX_LEN = 64
const VERSION_PATTERN = /^[0-9]+\.[0-9]+\.[0-9]+$/
const SKILL_ZIP_ERROR_KEYS: Record<string, string> = {
  INVALID_NAME: 'publish.skillErrorInvalidName',
  INVALID_VERSION: 'publish.skillErrorInvalidVersion',
  INVALID_DISPLAY_NAME: 'publish.skillErrorInvalidDisplayName',
  INVALID_DESCRIPTION: 'publish.skillErrorInvalidDescription',
  INVALID_SKILL_DESC: 'publish.skillErrorInvalidSkillDesc',
  INVALID_TAG: 'publish.skillErrorInvalidTag',
  INVALID_AUTHOR: 'publish.skillErrorInvalidAuthor',
  NO_SKILL_FILES: 'publish.skillErrorNoFiles',
  MISSING_RELATIVE_PATH: 'publish.skillErrorMissingRelativePath',
  SKILL_MD_NOT_AT_ROOT: 'publish.skillErrorSkillMdNotAtRoot',
  MISSING_SKILL_MD: 'publish.skillErrorMissingSkillMd',
  MISSING_SKILL_MD_DESCRIPTION: 'publish.skillErrorMissingSkillMdDescription',
  INVALID_SKILL_MD_FRONTMATTER_YAML: 'publish.skillErrorInvalidSkillMdFrontmatterYaml',
  INVALID_SKILL_KIND: 'publish.skillErrorInvalidSkillKind',
  ICON_NOT_PNG: 'publish.skillErrorIconNotPng',
  ICON_TOO_LARGE: 'publish.skillErrorIconTooLarge',
  TOO_MANY_ZIP_ENTRIES: 'publish.skillErrorTooManyEntries',
  TEAM_SKILL_ROLES_REQUIRED: 'publish.skillErrorTeamSkillRolesRequired',
  TEAM_SKILL_ROLES_MIN_2: 'publish.skillErrorTeamSkillRolesMin2',
  TEAM_SKILL_ROLE_NO_ID: 'publish.skillErrorTeamSkillRoleNoId',
  AGENT_ZIP_MISSING_PLUGIN_YAML: 'publish.agentErrorMissingPluginYaml',
  AGENT_ZIP_INVALID_PLUGIN_YAML: 'publish.agentErrorInvalidPluginYaml',
  AGENT_ZIP_UNSUPPORTED_TYPE: 'publish.agentErrorUnsupportedType',
  AGENT_ZIP_MISSING_NAME: 'publish.agentErrorMissingName',
  AGENT_ZIP_MISSING_VERSION: 'publish.agentErrorMissingVersion',
}

const ZIP_ERROR_TO_FIELD: Record<string, PublishFieldKey> = {
  INVALID_NAME: 'skillPkgName',
  INVALID_VERSION: 'pluginVersion',
  INVALID_DISPLAY_NAME: 'skillDisplayName',
  INVALID_DESCRIPTION: 'skillDescription',
  INVALID_SKILL_DESC: 'skillDescription',
  INVALID_TAG: 'skillTags',
  NO_SKILL_FILES: 'skillFolder',
  MISSING_RELATIVE_PATH: 'skillFolder',
  SKILL_MD_NOT_AT_ROOT: 'skillFolder',
  MISSING_SKILL_MD: 'skillFolder',
  MISSING_SKILL_MD_DESCRIPTION: 'skillFolder',
  INVALID_SKILL_MD_FRONTMATTER_YAML: 'skillFolder',
  INVALID_SKILL_KIND: 'skillFolder',
  TOO_MANY_ZIP_ENTRIES: 'skillFolder',
  TEAM_SKILL_ROLES_REQUIRED: 'skillFolder',
  TEAM_SKILL_ROLES_MIN_2: 'skillFolder',
  TEAM_SKILL_ROLE_NO_ID: 'skillFolder',
  ICON_NOT_PNG: 'skillIcon',
  ICON_TOO_LARGE: 'skillIcon',
}

type PublishFieldKey =
  | 'skillPkgName'
  | 'pluginVersion'
  | 'skillDisplayName'
  | 'skillDescription'
  | 'skillTags'
  | 'skillFolder'
  | 'skillIcon'

type PublishFieldErrors = Partial<Record<PublishFieldKey, string>>

type SkillVisibility = 'public' | 'private'

type PublishFormProps = {
  type: PublishDrawerType
  onCancel: () => void
  onSuccess?: () => void
}

function validateSkillName(value: string): string | null {
  const trimmed = value.trim()
  if (!trimmed) return null
  if (trimmed.length > SKILL_NAME_MAX_LEN) return 'publish.skillErrorInvalidNameTooLong'
  if (!SKILL_NAME_PATTERN.test(trimmed)) return 'publish.skillErrorInvalidName'
  return null
}

function validatePluginVersion(value: string): string | null {
  const trimmed = value.trim()
  if (!trimmed) return null
  if (!VERSION_PATTERN.test(trimmed)) return 'publish.skillErrorInvalidVersion'
  return null
}

function fieldErrorMerge(
  prev: PublishFieldErrors,
  key: PublishFieldKey,
  errorMessage: string | null,
): PublishFieldErrors {
  if (errorMessage) {
    if (prev[key] === errorMessage) return prev
    return { ...prev, [key]: errorMessage }
  }
  if (!prev[key]) return prev
  const next = { ...prev }
  delete next[key]
  return next
}

function Field({
  label,
  required,
  hint,
  htmlFor,
  error,
  fieldKey,
  children,
}: {
  label: string
  required?: boolean
  hint?: string
  htmlFor?: string
  error?: string
  fieldKey?: PublishFieldKey
  children: React.ReactNode
}) {
  const hintId = hint ? `${fieldKey || htmlFor || label}-hint` : undefined
  const triggerRef = useRef<HTMLSpanElement | null>(null)
  const [tooltipOpen, setTooltipOpen] = useState(false)
  const [tooltipPos, setTooltipPos] = useState<{ left: number; top: number } | null>(null)

  useLayoutEffect(() => {
    if (!tooltipOpen || !triggerRef.current) return
    const update = () => {
      const rect = triggerRef.current?.getBoundingClientRect()
      if (!rect) return
      setTooltipPos({ left: rect.left + rect.width / 2, top: rect.top - 10 })
    }
    update()
    window.addEventListener('scroll', update, true)
    window.addEventListener('resize', update)
    return () => {
      window.removeEventListener('scroll', update, true)
      window.removeEventListener('resize', update)
    }
  }, [tooltipOpen])

  return (
    <div data-field-key={fieldKey} className="relative overflow-visible">
      <div className="mb-1.5 flex items-center gap-1.5 text-[12.5px] font-medium text-[#334155]">
        {required ? <span className="text-[#F43F5E]">*</span> : null}
        {htmlFor ? <label htmlFor={htmlFor}>{label}</label> : <span>{label}</span>}
        {hint ? (
          <span
            ref={triggerRef}
            className="relative inline-flex"
            onMouseEnter={() => setTooltipOpen(true)}
            onMouseLeave={() => setTooltipOpen(false)}
            onFocus={() => setTooltipOpen(true)}
            onBlur={() => setTooltipOpen(false)}
          >
            <button
              type="button"
              aria-label={label}
              aria-describedby={hintId}
              className="inline-flex h-4 w-4 items-center justify-center rounded-full text-[#94A3B8] transition-colors hover:text-[#64748B]"
            >
              <CircleHelp className="h-3.5 w-3.5" aria-hidden />
            </button>
            {tooltipOpen && tooltipPos
              ? createPortal(
                  <span
                    id={hintId}
                    role="tooltip"
                    className="pointer-events-none fixed z-[1000] block w-56 -translate-x-1/2 -translate-y-full rounded-lg bg-[#111827] px-2.5 py-2 text-center text-[11px] font-normal leading-4 text-white shadow-[0_12px_28px_rgba(15,23,42,0.28)]"
                    style={{ left: tooltipPos.left, top: tooltipPos.top }}
                  >
                    <span
                      aria-hidden
                      className="absolute left-1/2 top-full h-2.5 w-2.5 -translate-x-1/2 -translate-y-1/2 rotate-45 bg-[#111827]"
                    />
                    {hint}
                  </span>,
                  document.body,
                )
              : null}
          </span>
        ) : null}
      </div>
      {children}
      {error ? (
        <p className="mt-1.5 flex items-start gap-1 text-[12px] leading-[1.5] text-rose-600" role="alert">
          <span aria-hidden>⚠</span>
          <span>{error}</span>
        </p>
      ) : null}
    </div>
  )
}

const PUBLISH_FIELD_ORDER: PublishFieldKey[] = [
  'skillPkgName',
  'pluginVersion',
  'skillDisplayName',
  'skillDescription',
  'skillTags',
  'skillFolder',
  'skillIcon',
]

const inputBase =
  'block h-10 w-full rounded-xl border border-[#E5E7EB] bg-white px-3 text-[13px] text-[#111827] placeholder:text-[#9CA3AF] transition-colors hover:border-[#D1D5DB] focus:border-[#1E54F9] focus:outline-none focus:ring-2 focus:ring-[#DBE6FF] disabled:cursor-not-allowed disabled:bg-[#F8FAFC] disabled:text-[#94A3B8]'
const inputError =
  'block h-10 w-full rounded-xl border border-rose-300 bg-rose-50/30 px-3 text-[13px] text-[#111827] placeholder:text-rose-300/80 transition-colors hover:border-rose-400 focus:border-rose-500 focus:outline-none focus:ring-2 focus:ring-rose-100 disabled:cursor-not-allowed disabled:bg-[#F8FAFC] disabled:text-[#94A3B8]'
const textareaBase =
  'block w-full rounded-xl border border-[#E5E7EB] bg-white px-3 py-2.5 text-[13px] leading-[1.6] text-[#111827] placeholder:text-[#9CA3AF] transition-colors hover:border-[#D1D5DB] focus:border-[#1E54F9] focus:outline-none focus:ring-2 focus:ring-[#DBE6FF] disabled:cursor-not-allowed disabled:bg-[#F8FAFC] disabled:text-[#94A3B8]'
const textareaError =
  'block w-full rounded-xl border border-rose-300 bg-rose-50/30 px-3 py-2.5 text-[13px] leading-[1.6] text-[#111827] placeholder:text-rose-300/80 transition-colors hover:border-rose-400 focus:border-rose-500 focus:outline-none focus:ring-2 focus:ring-rose-100 disabled:cursor-not-allowed disabled:bg-[#F8FAFC] disabled:text-[#94A3B8]'

const inputCls = (hasError?: boolean) => (hasError ? inputError : inputBase)
const textareaCls = (hasError?: boolean) => (hasError ? textareaError : textareaBase)

export function PublishForm({ type, onCancel, onSuccess }: PublishFormProps) {
  const { t } = useTranslation()
  const { user, isAuthenticated } = useGitCodeAuth()
  const skillFolderInputId = useId()
  const skillIconInputId = useId()
  const skillNameId = useId()
  const skillVersionId = useId()
  const skillDisplayNameId = useId()
  const skillDescriptionId = useId()
  const skillTagsId = useId()
  const versionDescId = useId()
  const pluginLinkId = useId()
  const publishTypeId = useId()

  const [selectedType, setSelectedType] = useState<PublishDrawerType>(type)
  const [detectedType, setDetectedType] = useState<DetectedPublishPluginType>('unknown')
  const [typeMismatchError, setTypeMismatchError] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [checksum, setChecksum] = useState('')
  const [hashing, setHashing] = useState(false)
  const [pluginId, setPluginId] = useState('')
  const [pluginVersion, setPluginVersion] = useState('')
  const [versionDesc, setVersionDesc] = useState('')
  const [force, setForce] = useState(false)
  const [visibility, setVisibility] = useState<SkillVisibility>('public')
  const [uploading, setUploading] = useState(false)
  const [generalError, setGeneralError] = useState('')
  const [fieldErrors, setFieldErrors] = useState<PublishFieldErrors>({})
  const [successMsg, setSuccessMsg] = useState('')
  const [templateBusy, setTemplateBusy] = useState(false)
  const [templateError, setTemplateError] = useState('')
  const [skillPkgName, setSkillPkgName] = useState('')
  const [skillDisplayName, setSkillDisplayName] = useState('')
  const [skillDescription, setSkillDescription] = useState('')
  const [skillTagsInput, setSkillTagsInput] = useState('')
  const [skillIconFile, setSkillIconFile] = useState<File | null>(null)
  const [skillIconPreview, setSkillIconPreview] = useState('')
  const [skillFolderFiles, setSkillFolderFiles] = useState<File[] | null>(null)
  const [skillFolderInputKey, setSkillFolderInputKey] = useState(0)
  const [skillIconInputKey, setSkillIconInputKey] = useState(0)
  const [agentZipInputKey, setAgentZipInputKey] = useState(0)
  const [packing, setPacking] = useState(false)
  const prevSkillPluginIdRef = useRef('')
  const isAgentMode = isAgentAssetPluginType(selectedType)
  const agentZipInputId = useId()

  useEffect(() => {
    setSelectedType(type)
  }, [type])

  useEffect(() => {
    setSkillFolderFiles(null)
    setFile(null)
    setChecksum('')
    setDetectedType('unknown')
    setTypeMismatchError('')
    setSkillFolderInputKey(k => k + 1)
    setAgentZipInputKey(k => k + 1)
    if (isAgentAssetPluginType(selectedType)) {
      setSkillPkgName('')
      setPluginVersion('')
      setSkillDisplayName('')
      setSkillDescription('')
      setSkillTagsInput('')
      setSkillIconFile(null)
    }
  }, [selectedType])

  const clearFieldError = (key: PublishFieldKey) => {
    setFieldErrors(prev => fieldErrorMerge(prev, key, null))
  }

  const scrollToFirstError = () => {
    const doScroll = () => {
      for (const key of PUBLISH_FIELD_ORDER) {
        const wrapper = document.querySelector<HTMLElement>(`[data-field-key="${key}"]`)
        if (!wrapper || !wrapper.querySelector('[role="alert"]')) continue
        wrapper.scrollIntoView({ behavior: 'smooth', block: 'center' })
        const input = wrapper.querySelector<HTMLInputElement | HTMLTextAreaElement>(
          'input:not([type="file"]), textarea',
        )
        if (input) {
          try {
            input.focus({ preventScroll: true })
          } catch {}
        }
        return
      }
      const scrollRoot = document.querySelector<HTMLElement>('[data-publish-form-scroll]')
      scrollRoot?.scrollTo({ top: 0, behavior: 'smooth' })
    }
    requestAnimationFrame(() => requestAnimationFrame(doScroll))
  }

  const detectFieldByKeyword = (message: string): PublishFieldKey | null => {
    if (!message) return null
    if (/SKILL\.md|frontmatter|子目录|sub\s*dir|plugin\.yaml|skill slug|非隐藏子目录/i.test(message)) {
      return 'skillFolder'
    }
    if (/图标|\bicon\b|\.png\b|PNG/i.test(message)) return 'skillIcon'
    if (/(插件|skill).*(版本|version)|版本号|semver/i.test(message)) return 'pluginVersion'
    return null
  }

  const applyErrorFromCode = (rawCode: string, fallbackMsg: string) => {
    const code = (rawCode || '').trim()
    const enumField = ZIP_ERROR_TO_FIELD[code]
    const i18nKey = SKILL_ZIP_ERROR_KEYS[code]
    if (enumField || i18nKey) {
      const msg = i18nKey ? t(i18nKey) : code || fallbackMsg
      if (enumField) setFieldErrors(prev => ({ ...prev, [enumField]: msg }))
      else setGeneralError(msg)
      return
    }
    const msg = code || fallbackMsg
    const heuristicField = detectFieldByKeyword(msg)
    if (heuristicField) setFieldErrors(prev => ({ ...prev, [heuristicField]: msg }))
    else setGeneralError(msg)
  }

  const skillMetadataLocked = Boolean(pluginId.trim())

  useEffect(() => {
    if (!skillIconFile) {
      setSkillIconPreview('')
      return
    }
    const url = URL.createObjectURL(skillIconFile)
    setSkillIconPreview(url)
    return () => URL.revokeObjectURL(url)
  }, [skillIconFile])

  const { data: myPluginsRes, isLoading: myPluginsLoading } = useQuery(
    ['publish-my-plugins', user?.id, selectedType],
    () =>
      getPlugins({
        publisher_id: user!.id,
        page: 1,
        page_size: 100,
        plugin_type: selectedType,
      }),
    { enabled: Boolean(isAuthenticated && user?.id) },
  )

  const myPlugins = useMemo(() => {
    const items = myPluginsRes?.data?.items ?? []
    // 关联已有插件时按当前所选可见性过滤：不支持公开/私有切换，避免下拉出现另一种可见性的 skill
    return items
      .filter(p => (String(p.visibility || 'public').toLowerCase() === 'private' ? 'private' : 'public') === visibility)
      .sort((a, b) => {
        const na = (a.display_name || a.displayName || a.name || '').toLowerCase()
        const nb = (b.display_name || b.displayName || b.name || '').toLowerCase()
        return na.localeCompare(nb, undefined, { sensitivity: 'base' })
      })
  }, [myPluginsRes, visibility])

  const selectedExistingPlugin = useMemo(
    () => myPlugins.find(plugin => plugin.asset_id === pluginId.trim()),
    [myPlugins, pluginId],
  )

  useEffect(() => {
    const prev = prevSkillPluginIdRef.current
    const pid = pluginId.trim()
    if (pid) {
      const row = myPlugins.find(p => p.asset_id === pid)
      if (row) {
        setSkillPkgName(row.name ?? '')
        setSkillDisplayName(row.display_name || row.displayName || row.name || '')
        setSkillDescription(String(row.short_desc || row.shortDesc || '').trim())
        setSkillTagsInput((row.tags ?? []).filter(Boolean).join(', '))
        setVisibility(String(row.visibility || 'public').toLowerCase() === 'private' ? 'private' : 'public')
      }
    } else if (prev) {
      setSkillPkgName('')
      setSkillDisplayName('')
      setSkillDescription('')
      setSkillTagsInput('')
      setVisibility('public')
    }
    prevSkillPluginIdRef.current = pluginId
  }, [pluginId, myPlugins])

  useEffect(() => {
    setPluginId('')
  }, [selectedType])

  useEffect(() => {
    if (!skillFolderFiles?.length || isAgentMode) {
      if (!isAgentMode) {
        setDetectedType('unknown')
        setTypeMismatchError('')
      }
      return
    }
    let cancelled = false
    void (async () => {
      try {
        const nextDetectedType = await detectPublishPluginType(skillFolderFiles)
        if (cancelled) return
        setDetectedType(nextDetectedType)
        setTypeMismatchError(nextDetectedType !== selectedType ? t('publish.typeMismatchError') : '')
      } catch (e) {
        if (cancelled) return
        setDetectedType('unknown')
        setTypeMismatchError('')
        applyErrorFromCode(e instanceof Error ? e.message : '', t('publish.skillPackFailed'))
      }
    })()
    return () => {
      cancelled = true
    }
  }, [skillFolderFiles, selectedType, t, isAgentMode])

  // Sync tags input from SKILL.md frontmatter for new skill publish (no existing plugin_id).
  // When publishing a new version of an existing skill, tags come from DB (the pluginId effect).
  // Unconditionally set (including clearing to '') so switching to a folder without tags
  // does not leave stale tags from a previous folder in the input.
  useEffect(() => {
    if (!skillFolderFiles?.length || pluginId.trim()) return
    const skillMdFile = skillFolderFiles.find(f => {
      const wrp = (f as File & { webkitRelativePath?: string }).webkitRelativePath
      if (!wrp) return false
      const rel = wrp.replace(/\\/g, '/').replace(/^\//, '')
      const parts = rel.split('/').filter(Boolean)
      return parts.length >= 2 && parts[parts.length - 1] === 'SKILL.md'
    })
    if (!skillMdFile) {
      setSkillTagsInput('')
      return
    }
    let cancelled = false
    void (async () => {
      try {
        const raw = await skillMdFile.text()
        const fm = parseSkillMdFrontmatter(raw)
        if (cancelled) return
        setSkillTagsInput(fm.tags && fm.tags.length > 0 ? fm.tags.join(', ') : '')
      } catch {
        if (cancelled) return
        setSkillTagsInput('')
      }
    })()
    return () => { cancelled = true }
  }, [skillFolderFiles, pluginId])

  const skillFolderRootName = useMemo(() => {
    const first = skillFolderFiles?.[0] as (File & { webkitRelativePath?: string }) | undefined
    const p = first?.webkitRelativePath
    if (!p) return ''
    return p.split(/[/\\]/).filter(Boolean)[0] ?? ''
  }, [skillFolderFiles])

  const skillFormReady = useMemo(() => {
    const login = user?.login?.trim()
    if (isAgentMode) {
      return Boolean(login && file && skillPkgName.trim() && pluginVersion.trim() && !typeMismatchError)
    }
    return Boolean(
      login &&
        skillFolderFiles &&
        skillFolderFiles.length > 0 &&
        skillPkgName.trim() &&
        pluginVersion.trim() &&
        skillDisplayName.trim(),
    )
  }, [
    user?.login,
    isAgentMode,
    file,
    skillFolderFiles,
    skillPkgName,
    pluginVersion,
    skillDisplayName,
    typeMismatchError,
  ])

  useEffect(() => {
    if (isAgentMode) {
      setPacking(false)
      return
    }
    if (!skillFormReady || typeMismatchError) {
      setPacking(false)
      setFile(null)
      return
    }
    const login = user?.login?.trim()
    if (!login) {
      setPacking(false)
      setFile(null)
      return
    }
    let cancelled = false
    setPacking(true)
    const timer = window.setTimeout(() => {
      void (async () => {
        try {
          const tags = skillTagsInput.split(/[,，]/).map(s => s.trim()).filter(Boolean)
          const zipFile = await buildSkillPublishZip({
            name: skillPkgName.trim(),
            version: pluginVersion.trim(),
            displayName: skillDisplayName.trim(),
            description: skillDescription.trim() || undefined,
            tags,
            authorLogin: login,
            iconFile: skillIconFile ?? undefined,
            skillDirectoryFiles: skillFolderFiles!,
          })
          if (cancelled) return
          setFile(zipFile)
          setGeneralError('')
          setFieldErrors(prev => {
            const packagingKeys = new Set(Object.values(ZIP_ERROR_TO_FIELD))
            packagingKeys.delete('skillIcon')
            const next = { ...prev }
            for (const key of packagingKeys) delete next[key]
            return next
          })
        } catch (e) {
          if (cancelled) return
          setFile(null)
          applyErrorFromCode(e instanceof Error ? e.message : '', t('publish.skillPackFailed'))
        } finally {
          if (!cancelled) setPacking(false)
        }
      })()
    }, 400)
    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [
    isAgentMode,
    skillFormReady,
    typeMismatchError,
    user?.login,
    skillIconFile,
    skillFolderFiles,
    skillPkgName,
    pluginVersion,
    skillDisplayName,
    skillDescription,
    skillTagsInput,
    t,
  ])

  useEffect(() => {
    if (!file) {
      setChecksum('')
      return
    }
    let cancelled = false
    setHashing(true)
    void sha256HexOfFile(file)
      .then(hex => {
        if (!cancelled) {
          setChecksum(hex)
          setGeneralError('')
        }
      })
      .catch(() => {
        if (!cancelled) {
          setChecksum('')
          setGeneralError(t('publish.hashFailed'))
        }
      })
      .finally(() => {
        if (!cancelled) setHashing(false)
      })
    return () => {
      cancelled = true
    }
  }, [file, t])

  const blockingFieldErrors = useMemo(() => {
    const next: PublishFieldErrors = { ...fieldErrors }
    if (!skillIconFile && next.skillIcon) delete next.skillIcon
    return next
  }, [fieldErrors, skillIconFile])

  const canSubmit = Boolean(
    skillFormReady &&
      !uploading &&
      !successMsg &&
      Object.keys(blockingFieldErrors).length === 0 &&
      !typeMismatchError,
  )

  const fieldErrorCount = PUBLISH_FIELD_ORDER.reduce(
    (n, k) => (blockingFieldErrors[k] ? n + 1 : n),
    0,
  )
  const fieldErrorLabels = PUBLISH_FIELD_ORDER.filter(k => blockingFieldErrors[k]).map(k => fieldLabelByKey[k])

  const typeOptions = [
    { value: 'swarmskill' as const, label: t('publish.typeSwarmSkill') },
    { value: 'skill' as const, label: t('publish.typeSkill') },
    { value: 'agent-plugin' as const, label: t('publish.typeAgentPlugin') },
    { value: 'agent-template' as const, label: t('publish.typeAgentTemplate') },
    { value: 'agent-mcp' as const, label: t('publish.typeAgentMcp') },
  ]
  const formFieldLabels = useMemo(() => resolvePublishFormFieldLabels(selectedType, t), [selectedType, t])
  const fieldLabelByKey = useMemo(
    (): Record<PublishFieldKey, string> => ({
      skillPkgName: formFieldLabels.pkgName,
      pluginVersion: t('publish.fieldVersionSkill'),
      skillDisplayName: formFieldLabels.displayName,
      skillDescription: formFieldLabels.description,
      skillTags: formFieldLabels.tags,
      skillFolder: formFieldLabels.folder,
      skillIcon: formFieldLabels.icon,
    }),
    [formFieldLabels, t],
  )
  const detectedTypeLabel =
    detectedType === 'swarmskill'
      ? t('publish.typeSwarmSkill')
      : detectedType === 'skill'
        ? t('publish.typeSkill')
        : detectedType === 'agent-plugin'
          ? t('publish.typeAgentPlugin')
          : detectedType === 'agent-template'
            ? t('publish.typeAgentTemplate')
            : detectedType === 'agent-mcp'
              ? t('publish.typeAgentMcp')
              : t('publish.typeUnknown')
  const pluginLinkLabel = formFieldLabels.pluginLinkLabel
  const pluginLinkHint = myPluginsLoading ? t('publish.pluginListLoading') : formFieldLabels.pluginLinkHint
  const pluginNewOptionLabel = formFieldLabels.pluginNewOptionLabel
  const versionDescLabel =
    selectedType === 'swarmskill' ? t('publish.fieldVersionDescSwarmSkill') : t('publish.fieldVersionDesc')
  const publishButtonLabel = isAgentMode
    ? t('publish.publishAgent')
    : selectedType === 'swarmskill'
      ? t('publish.publishSwarmSkill')
      : t('appHeader.publish')

  const handleAgentZipSelected = async (nextFile: File | null) => {
    setGeneralError('')
    setFieldErrors({})
    setFile(null)
    setChecksum('')
    setDetectedType('unknown')
    setTypeMismatchError('')
    if (!nextFile) return
    try {
      const info = await inspectAgentPublishZip(nextFile)
      setDetectedType(info.pluginType)
      setTypeMismatchError(info.pluginType !== selectedType ? t('publish.typeMismatchError') : '')
      setSkillPkgName(info.name)
      setPluginVersion(info.version)
      setSkillDisplayName(info.displayName || info.name)
      setSkillDescription(info.description)
      setFile(nextFile)
    } catch (e) {
      applyErrorFromCode(e instanceof Error ? e.message : '', t('publish.agentZipInspectFailed'))
    }
  }

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault()
    if (!skillFormReady || uploading || successMsg || typeMismatchError) return

    if (!isAgentMode) {
      const nameErrorKey = validateSkillName(skillPkgName)
      if (nameErrorKey) {
        setFieldErrors(prev => ({ ...prev, skillPkgName: t(nameErrorKey) }))
        scrollToFirstError()
        return
      }
    }
    const versionErrorKey = validatePluginVersion(pluginVersion)
    if (versionErrorKey) {
      setFieldErrors(prev => ({ ...prev, pluginVersion: t(versionErrorKey) }))
      scrollToFirstError()
      return
    }

    const pluginVersionNormalized = normalizeSemver(pluginVersion.trim())
    setUploading(true)
    setGeneralError('')
    setFieldErrors({})
    setSuccessMsg('')
    try {
      const login = user?.login?.trim()
      if (!login) {
        setGeneralError(t('publish.uploadFailed'))
        return
      }
      let zipFile: File
      if (isAgentMode) {
        if (!file) {
          setGeneralError(t('publish.agentZipRequired'))
          return
        }
        zipFile = file
      } else {
        if (!skillFolderFiles?.length) {
          setGeneralError(t('publish.uploadFailed'))
          return
        }
        const tags = skillTagsInput.split(/[,，]/).map(s => s.trim()).filter(Boolean)
        zipFile = await buildSkillPublishZip({
          name: skillPkgName.trim(),
          version: pluginVersion.trim(),
          displayName: skillDisplayName.trim(),
          description: skillDescription.trim() || undefined,
          tags,
          authorLogin: login,
          iconFile: skillIconFile ?? undefined,
          skillDirectoryFiles: skillFolderFiles,
        })
      }
      const checksumFresh = await sha256HexOfFile(zipFile)
      const data = await publishPlugin({
        file: zipFile,
        checksumSha256Hex: checksumFresh,
        pluginId: pluginId.trim() || undefined,
        pluginVersion: pluginVersionNormalized,
        versionDesc: versionDesc.trim() || undefined,
        force,
        visibility,
      })
      setFile(null)
      setChecksum('')
      setPluginId('')
      setPluginVersion('')
      setVersionDesc('')
      setForce(false)
      setVisibility('public')
      setSkillPkgName('')
      setSkillDisplayName('')
      setSkillDescription('')
      setSkillTagsInput('')
      setSkillIconFile(null)
      setFieldErrors({})
      setSkillFolderFiles(null)
      setDetectedType('unknown')
      setTypeMismatchError('')
      setSkillFolderInputKey(k => k + 1)
      setSkillIconInputKey(k => k + 1)
      setAgentZipInputKey(k => k + 1)
      setSuccessMsg(
        t('publish.successDetail', {
          name: data.name,
          version: formatSkillVersionLabel(data.version),
          pluginId: data.plugin_id,
        }),
      )
      onSuccess?.()
    } catch (err) {
      if (
        err instanceof MarketplaceApiError &&
        (err.errorType === 'version_conflict' || err.errorType === 'version_exists')
      ) {
        setGeneralError(
          t('publish.versionConflict', {
            name: skillPkgName.trim(),
            version: pluginVersionNormalized,
          }),
        )
      } else if (err instanceof MarketplaceApiError) {
        setGeneralError(err.message || t('publish.uploadFailed'))
      } else {
        applyErrorFromCode(err instanceof Error ? err.message : '', t('publish.uploadFailed'))
      }
      scrollToFirstError()
    } finally {
      setUploading(false)
    }
  }

  const handleSuccessConfirm = () => {
    setSuccessMsg('')
    onCancel()
  }

  useEffect(() => {
    if (!successMsg) return
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' || e.key === 'Enter') {
        e.preventDefault()
        e.stopPropagation()
        handleSuccessConfirm()
      }
    }
    document.addEventListener('keydown', onKeyDown, true)
    return () => document.removeEventListener('keydown', onKeyDown, true)
  }, [successMsg])

  const onDownloadTemplate = async () => {
    if (isAgentMode) return
    setTemplateError('')
    setTemplateBusy(true)
    try {
      const { download_url: url } = await getPublishTemplatePresigned({ kind: selectedType === 'swarmskill' ? 'swarmskill' : 'skill' })
      window.location.assign(url)
    } catch (err) {
      setTemplateError(err instanceof Error ? err.message : t('publish.templateDownloadFailed'))
    } finally {
      setTemplateBusy(false)
    }
  }

  return (
    <>
      <form onSubmit={onSubmit} className="flex min-h-0 flex-1 flex-col" aria-busy={uploading || packing || hashing}>
        <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-5 pb-5 pt-4 sm:px-6" data-publish-form-scroll>
          {generalError ? (
            <div className="rounded-xl border border-rose-200 bg-rose-50/80 px-3.5 py-2.5 text-[12.5px] leading-5 text-rose-800 whitespace-pre-wrap">
              {generalError}
            </div>
          ) : null}
          {templateError ? (
            <div className="rounded-xl border border-amber-200 bg-amber-50/80 px-3.5 py-2.5 text-[12.5px] leading-5 text-amber-900">
              {templateError}
            </div>
          ) : null}

          <Field htmlFor={publishTypeId} label={t('publish.typeSelectorLabel')} hint={t('publish.typeSelectionHint')}>
            <div className="space-y-2">
              <select
                id={publishTypeId}
                value={selectedType}
                onChange={e => setSelectedType(e.target.value as PublishDrawerType)}
                className={`${inputBase} max-w-[408px]`}
              >
                {typeOptions.map(option => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
              <div className={`text-[12px] ${detectedType === 'unknown' ? 'text-[#94A3B8]' : 'text-[#0F172A]'}`}>
                {t('publish.typeDetectedHint', { type: detectedTypeLabel })}
              </div>
              {typeMismatchError ? (
                <div className="rounded-xl border border-rose-200 bg-rose-50/70 px-3 py-2 text-[12px] leading-5 text-rose-700">
                  {typeMismatchError}
                </div>
              ) : null}
            </div>
          </Field>


          <Field label={t('publish.visibilityLabel')} hint={t('publish.visibilityHint')}>
            <div className="grid max-w-[408px] grid-cols-2 gap-2">
              {(['public', 'private'] as SkillVisibility[]).map(value => {
                const active = visibility === value
                return (
                  <button
                    key={value}
                    type="button"
                    onClick={() => {
                      if (!selectedExistingPlugin) setVisibility(value)
                    }}
                    disabled={Boolean(selectedExistingPlugin)}
                    className={`rounded-xl border px-3 py-2 text-left transition-colors disabled:cursor-not-allowed ${
                      active
                        ? 'border-[#1E54F9] bg-[#F5F8FF] text-[#0F172A]'
                        : 'border-[#E5E7EB] bg-white text-[#64748B] hover:border-[#CBD5E1]'
                    }`}
                    aria-pressed={active}
                  >
                    <span className="block text-[13px] font-semibold">{t(`publish.visibility.${value}.label`)}</span>
                    <span className="mt-0.5 block text-[11.5px] leading-4">{t(`publish.visibility.${value}.help`)}</span>
                  </button>
                )
              })}
            </div>
            {selectedExistingPlugin ? (
              <div className="mt-2 text-[12px] text-[#64748B]">{t('publish.visibilityLockedHint')}</div>
            ) : null}
          </Field>

          <Field htmlFor={pluginLinkId} label={pluginLinkLabel} hint={pluginLinkHint}>
            <select id={pluginLinkId} value={pluginId} onChange={e => setPluginId(String(e.target.value))} className={inputBase}>
              <option value="">{pluginNewOptionLabel}</option>
              {myPlugins.map(p => {
                const title = p.display_name || p.displayName || p.name
                const pkgName = p.name?.trim() || '—'
                return (
                  <option key={p.asset_id} value={p.asset_id}>
                    {title}
                    {pkgName && pkgName !== title ? ` · ${pkgName}` : ''}
                  </option>
                )
              })}
            </select>
          </Field>

          {skillMetadataLocked ? (
            <div className="-mt-2 rounded-xl border border-sky-200/70 bg-sky-50/70 px-3 py-2 text-[11.5px] leading-5 text-[#0C4A8A]">
              {formFieldLabels.metadataLockedHint}
            </div>
          ) : null}

          {isAgentMode ? (
            <Field label={t('publish.fieldAgentZip')} required hint={t('publish.fieldAgentZipHelp')} error={fieldErrors.skillFolder} fieldKey="skillFolder">
              <input
                key={agentZipInputKey}
                id={agentZipInputId}
                type="file"
                accept=".zip,application/zip"
                className="sr-only"
                onClick={e => {
                  e.currentTarget.value = ''
                }}
                onChange={e => {
                  const f = e.target.files?.[0] ?? null
                  void handleAgentZipSelected(f)
                }}
              />
              <label
                htmlFor={agentZipInputId}
                className={`flex cursor-pointer flex-col items-center justify-center gap-2 rounded-xl border border-dashed px-4 py-5 text-center transition-colors ${
                  fieldErrors.skillFolder
                    ? 'border-rose-300 bg-rose-50/40 hover:border-rose-400'
                    : file
                      ? 'border-[#CBD5E1] bg-[#F8FAFC]'
                      : 'border-[#CBD5E1] hover:border-[#1E54F9] hover:bg-[#F5F8FF]'
                }`}
              >
                <span className={`inline-flex h-9 w-9 items-center justify-center rounded-full ${file ? 'bg-emerald-50 text-emerald-600' : 'bg-[#EEF4FF] text-[#1E54F9]'}`}>
                  {file ? <CheckCircle2 className="h-4 w-4" aria-hidden /> : <UploadCloud className="h-4 w-4" aria-hidden />}
                </span>
                {file ? (
                  <div className="space-y-0.5">
                    <div className="text-[13px] font-medium text-[#0F172A]">{file.name}</div>
                    <div className="text-[11.5px] text-[#64748B]">{t('publish.agentZipSelected')}</div>
                  </div>
                ) : (
                  <div className="space-y-0.5">
                    <div className="text-[13px] font-medium text-[#334155]">{t('publish.agentZipChoose')}</div>
                    <div className="text-[11.5px] text-[#94A3B8]">{t('publish.agentZipNone')}</div>
                  </div>
                )}
              </label>
            </Field>
          ) : null}

          <Field htmlFor={skillNameId} label={formFieldLabels.pkgName} required hint={formFieldLabels.pkgNameHelp} error={fieldErrors.skillPkgName} fieldKey="skillPkgName">
            <input
              id={skillNameId}
              type="text"
              className={inputCls(Boolean(fieldErrors.skillPkgName))}
              value={skillPkgName}
              onChange={e => {
                const val = e.target.value
                setSkillPkgName(val)
                const skKey = validateSkillName(val)
                setFieldErrors(prev => fieldErrorMerge(prev, 'skillPkgName', skKey ? t(skKey) : null))
              }}
              disabled={skillMetadataLocked || isAgentMode}
              placeholder={formFieldLabels.pkgNamePlaceholder}
              required
              aria-invalid={Boolean(fieldErrors.skillPkgName)}
            />
          </Field>

          <Field htmlFor={skillVersionId} label={t('publish.fieldVersionSkill')} required hint={formFieldLabels.versionHelp} error={fieldErrors.pluginVersion} fieldKey="pluginVersion">
            <input
              id={skillVersionId}
              type="text"
              className={inputCls(Boolean(fieldErrors.pluginVersion))}
              value={pluginVersion}
              onChange={e => {
                const val = e.target.value
                setPluginVersion(val)
                const errKey = validatePluginVersion(val)
                setFieldErrors(prev => fieldErrorMerge(prev, 'pluginVersion', errKey ? t(errKey) : null))
              }}
              placeholder="1.0.0"
              required
              disabled={isAgentMode}
              aria-invalid={Boolean(fieldErrors.pluginVersion)}
            />
          </Field>

          <Field htmlFor={skillDisplayNameId} label={formFieldLabels.displayName} required={!isAgentMode} hint={formFieldLabels.displayNameHelp} error={fieldErrors.skillDisplayName} fieldKey="skillDisplayName">
            <input
              id={skillDisplayNameId}
              type="text"
              className={inputCls(Boolean(fieldErrors.skillDisplayName))}
              value={skillDisplayName}
              onChange={e => {
                setSkillDisplayName(e.target.value)
                clearFieldError('skillDisplayName')
              }}
              disabled={skillMetadataLocked || isAgentMode}
              placeholder={formFieldLabels.displayName}
              required={!isAgentMode}
              aria-invalid={Boolean(fieldErrors.skillDisplayName)}
            />
          </Field>

          {!isAgentMode ? (
            <>
          <Field htmlFor={skillDescriptionId} label={formFieldLabels.description} hint={formFieldLabels.descriptionHelp} error={fieldErrors.skillDescription} fieldKey="skillDescription">
            <textarea
              id={skillDescriptionId}
              className={textareaCls(Boolean(fieldErrors.skillDescription))}
              rows={3}
              value={skillDescription}
              onChange={e => {
                setSkillDescription(e.target.value)
                clearFieldError('skillDescription')
              }}
              placeholder={formFieldLabels.descriptionPlaceholder}
              aria-invalid={Boolean(fieldErrors.skillDescription)}
            />
          </Field>

          <Field htmlFor={skillTagsId} label={formFieldLabels.tags} hint={formFieldLabels.tagsHelp} error={fieldErrors.skillTags} fieldKey="skillTags">
            <input
              id={skillTagsId}
              type="text"
              className={inputCls(Boolean(fieldErrors.skillTags))}
              value={skillTagsInput}
              onChange={e => {
                setSkillTagsInput(e.target.value)
                clearFieldError('skillTags')
              }}
              placeholder="tag1, tag2"
              aria-invalid={Boolean(fieldErrors.skillTags)}
            />
          </Field>

          <Field label={formFieldLabels.folder} required hint={formFieldLabels.folderHelp} error={fieldErrors.skillFolder} fieldKey="skillFolder">
            <input
              key={skillFolderInputKey}
              id={skillFolderInputId}
              type="file"
              className="sr-only"
              // @ts-expect-error webkitdirectory 非标准属性，用于选择文件夹
              webkitdirectory=""
              multiple
              onClick={e => {
                e.currentTarget.value = ''
              }}
              onChange={e => {
                const list = e.target.files
                setSkillFolderFiles(list && list.length ? Array.from(list) : null)
                clearFieldError('skillFolder')
              }}
            />
            <label
              htmlFor={skillFolderInputId}
              className={`flex cursor-pointer flex-col items-center justify-center gap-2 rounded-xl border border-dashed px-4 py-5 text-center transition-colors ${
                fieldErrors.skillFolder
                  ? 'border-rose-300 bg-rose-50/40 hover:border-rose-400'
                  : skillFolderFiles?.length
                    ? 'border-[#CBD5E1] bg-[#F8FAFC]'
                    : 'border-[#CBD5E1] hover:border-[#1E54F9] hover:bg-[#F5F8FF]'
              }`}
            >
              <span className={`inline-flex h-9 w-9 items-center justify-center rounded-full ${skillFolderFiles?.length ? 'bg-emerald-50 text-emerald-600' : 'bg-[#EEF4FF] text-[#1E54F9]'}`}>
                {skillFolderFiles?.length ? <CheckCircle2 className="h-4 w-4" aria-hidden /> : <FolderUp className="h-4 w-4" aria-hidden />}
              </span>
              {skillFolderFiles?.length ? (
                <div className="space-y-0.5">
                  <div className="text-[13px] font-medium text-[#0F172A]">{skillFolderRootName || '—'}</div>
                  <div className="text-[11.5px] text-[#64748B]">{t('publish.skillFolderSelected', { name: skillFolderRootName || '—' })}</div>
                </div>
              ) : (
                <div className="space-y-0.5">
                  <div className="text-[13px] font-medium text-[#334155]">{t('publish.skillFolderChoose')}</div>
                  <div className="text-[11.5px] text-[#94A3B8]">{t('publish.skillFolderNone')}</div>
                </div>
              )}
            </label>
          </Field>

          <Field label={formFieldLabels.icon} hint={formFieldLabels.iconHelp} error={fieldErrors.skillIcon} fieldKey="skillIcon">
            <input
              key={skillIconInputKey}
              id={skillIconInputId}
              type="file"
              accept="image/png,.png"
              className="sr-only"
              onChange={e => {
                const f = e.target.files?.[0] ?? null
                if (!f) {
                  setSkillIconFile(null)
                  clearFieldError('skillIcon')
                  return
                }
                const isPng = f.type === 'image/png' || f.name.toLowerCase().endsWith('.png')
                if (!isPng) {
                  setSkillIconFile(null)
                  setFieldErrors(prev => ({ ...prev, skillIcon: t('publish.skillErrorIconNotPng') }))
                  setSkillIconInputKey(k => k + 1)
                  return
                }
                if (f.size > 5 * 1024 * 1024) {
                  setSkillIconFile(null)
                  setFieldErrors(prev => ({ ...prev, skillIcon: t('publish.skillErrorIconTooLarge') }))
                  setSkillIconInputKey(k => k + 1)
                  return
                }
                clearFieldError('skillIcon')
                setSkillIconFile(f)
              }}
            />
            <div className={`flex items-center gap-3 rounded-xl p-1 ${fieldErrors.skillIcon ? 'bg-rose-50/50 ring-1 ring-rose-200' : ''}`}>
              <label
                htmlFor={skillIconInputId}
                className={`group relative inline-flex h-[72px] w-[72px] shrink-0 cursor-pointer items-center justify-center overflow-hidden rounded-xl border border-dashed transition-colors ${
                  fieldErrors.skillIcon
                    ? 'border-rose-300 bg-rose-50'
                    : 'border-[#CBD5E1] bg-[#F8FAFC] hover:border-[#1E54F9] hover:bg-[#F5F8FF]'
                }`}
              >
                {skillIconPreview ? (
                  <img src={skillIconPreview} alt="" aria-hidden className="h-full w-full object-cover" />
                ) : (
                  <ImagePlus className={`h-5 w-5 transition-colors ${fieldErrors.skillIcon ? 'text-rose-400' : 'text-[#94A3B8] group-hover:text-[#1E54F9]'}`} aria-hidden />
                )}
              </label>
              <div className="min-w-0 text-[12px] text-[#64748B]">
                <div className="truncate text-[13px] font-medium text-[#0F172A]">{skillIconFile?.name ?? formFieldLabels.icon}</div>
                <div className="mt-0.5">PNG · ≤ 5MB</div>
              </div>
            </div>
          </Field>
            </>
          ) : null}

          <Field label={t('publish.checksumLabel')} hint={t('publish.checksumHintSkill')}>
            <div className="mb-1.5 flex items-center justify-end gap-2">
              {packing ? (
                <span className="inline-flex items-center gap-1 text-[11px] text-[#1E54F9]">
                  <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
                  {t('publish.skillPacking')}
                </span>
              ) : hashing ? (
                <span className="inline-flex items-center gap-1 text-[11px] text-[#1E54F9]">
                  <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
                  {t('publish.hashing')}
                </span>
              ) : checksum ? (
                <span className="inline-flex items-center gap-1 text-[11px] text-emerald-600">
                  <CheckCircle2 className="h-3 w-3" aria-hidden />
                  {t('publish.checksumReady')}
                </span>
              ) : null}
            </div>
            <input
              type="text"
              readOnly
              tabIndex={-1}
              value={checksum || ''}
              placeholder={t('publish.checksumPlaceholderSkill')}
              className="block h-10 w-full rounded-xl border border-[#CBD5E1] bg-white px-3 font-mono text-[11px] leading-[1.6] tracking-[0.01em] text-[#334155] placeholder:text-[#94A3B8] focus:outline-none"
            />
          </Field>

          <Field htmlFor={versionDescId} label={versionDescLabel}>
            <textarea
              id={versionDescId}
              className={textareaBase}
              rows={3}
              maxLength={1000}
              value={versionDesc}
              onChange={e => setVersionDesc(e.target.value)}
              placeholder={versionDescLabel}
            />
            <p className="mt-1 text-right text-[11px] text-[#94A3B8]">{versionDesc.length}/1000</p>
          </Field>

          <label className="flex cursor-pointer items-center gap-2 text-[13px] text-[#334155]">
            <input
              type="checkbox"
              checked={force}
              onChange={e => setForce(e.target.checked)}
              className="h-4 w-4 rounded border-[#CBD5E1] text-[#1E54F9] focus:ring-[#DBE6FF]"
            />
            <span>{t('publish.fieldForce')}</span>
          </label>
        </div>

        {fieldErrorCount > 0 ? (
          <div className="shrink-0 border-t border-rose-100 bg-rose-50/85 px-5 py-2.5 sm:px-6">
            <button type="button" onClick={scrollToFirstError} className="group flex w-full items-center justify-between gap-3 text-left">
              <span className="flex min-w-0 items-center gap-2 text-[12.5px] leading-5 text-rose-700">
                <span aria-hidden className="inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-rose-100 text-rose-600">⚠</span>
                <span className="truncate">
                  {t('publish.fieldErrorsSummary', { count: fieldErrorCount })}
                  <span className="text-rose-500/80">{' · '}{fieldErrorLabels.join(', ')}</span>
                </span>
              </span>
              <span className="shrink-0 text-[12px] font-medium text-rose-600 group-hover:underline">{t('publish.jumpToFirstError')}</span>
            </button>
          </div>
        ) : null}

        <footer className="shrink-0 border-t border-slate-200 bg-white px-5 py-3 sm:px-6">
          <div className="flex items-center justify-end gap-2.5">
            <button
              type="button"
              onClick={onCancel}
              disabled={uploading}
              className="inline-flex h-9 min-w-[88px] items-center justify-center rounded-full border border-[#E5E7EB] bg-white px-4 text-[13px] font-medium text-[#4B5563] transition-colors hover:border-[#D1D5DB] hover:text-[#111827] disabled:opacity-60"
            >
              {t('profile.card.cancel')}
            </button>
            <button
              type="submit"
              disabled={!canSubmit}
              title={typeMismatchError || undefined}
              className="inline-flex h-9 min-w-[108px] items-center justify-center gap-1.5 rounded-full bg-[linear-gradient(99.61deg,#1E54F9_0%,#852EFE_100%)] px-4 text-[13px] font-medium text-white shadow-[0_4px_10px_-6px_rgba(30,84,249,0.42)] transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50 disabled:shadow-none"
            >
              {uploading ? (
                <>
                  <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
                  <span>{t('publish.uploading')}</span>
                </>
              ) : (
                <>
                  <UploadCloud className="h-3.5 w-3.5" aria-hidden />
                  <span>{publishButtonLabel}</span>
                </>
              )}
            </button>
          </div>
        </footer>
      </form>

      {successMsg ? (
        <div className="fixed inset-0 z-[60] flex items-center justify-center px-4" role="dialog" aria-modal="true" aria-labelledby="publish-success-dialog-title">
          <div className="absolute inset-0 bg-slate-900/55 backdrop-blur-[2px] animate-notice-in" />
          <div className="relative w-full max-w-[400px] overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-[0_20px_40px_-18px_rgba(15,23,42,0.28)] animate-notice-in">
            <div className="flex flex-col items-center gap-3 px-6 pb-5 pt-7 text-center">
              <span className="inline-flex h-11 w-11 items-center justify-center rounded-full bg-emerald-50 ring-1 ring-emerald-100">
                <CheckCircle2 className="h-6 w-6 text-emerald-600" aria-hidden />
              </span>
              <h3 id="publish-success-dialog-title" className="text-[15px] font-semibold text-[#111827]">{t('publish.successDialogTitle')}</h3>
              <p className="text-[12.5px] leading-[1.65] text-[#4B5563]">{successMsg}</p>
              <p className="text-[11.5px] leading-[1.5] text-[#9CA3AF]">{t('publish.successDialogHint')}</p>
            </div>
            <div className="flex items-center justify-end gap-2 border-t border-slate-100 bg-white px-5 py-3">
              <button
                type="button"
                onClick={handleSuccessConfirm}
                autoFocus
                className="inline-flex h-9 min-w-[88px] items-center justify-center rounded-full bg-[linear-gradient(99.61deg,#1E54F9_0%,#852EFE_100%)] px-4 text-[13px] font-medium text-white shadow-[0_4px_10px_-6px_rgba(30,84,249,0.42)] transition-opacity hover:opacity-90 focus:outline-none focus-visible:ring-2 focus-visible:ring-[#c7d2fe]"
              >
                {t('publish.successDialogConfirm')}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </>
  )
}

export default PublishForm
