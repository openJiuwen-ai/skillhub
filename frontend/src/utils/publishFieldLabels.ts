// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import type { TFunction } from 'i18next'
import type { PublishDrawerType } from '@/contexts/PublishDrawer'
import { isAgentAssetPluginType } from '@/utils/pluginType'

export type PublishFormFieldLabels = {
  pkgName: string
  pkgNameHelp: string
  pkgNamePlaceholder: string
  versionHelp: string
  displayName: string
  displayNameHelp: string
  description: string
  descriptionHelp: string
  descriptionPlaceholder: string
  tags: string
  tagsHelp: string
  folder: string
  folderHelp: string
  icon: string
  iconHelp: string
  metadataLockedHint: string
  pluginLinkLabel: string
  pluginLinkHint: string
  pluginNewOptionLabel: string
}

function agentPluginLinkKeys(type: PublishDrawerType): {
  label: string
  hint: string
  newOption: string
  metadataLocked: string
} {
  if (type === 'agent-template') {
    return {
      label: 'publish.fieldPluginIdAgentTemplate',
      hint: 'publish.fieldPluginIdHelpAgentTemplate',
      newOption: 'publish.pluginIdNewOptionAgentTemplate',
      metadataLocked: 'publish.fieldMetadataLockedHintAgentTemplate',
    }
  }
  if (type === 'agent-mcp') {
    return {
      label: 'publish.fieldPluginIdAgentMcp',
      hint: 'publish.fieldPluginIdHelpAgentMcp',
      newOption: 'publish.pluginIdNewOptionAgentMcp',
      metadataLocked: 'publish.fieldMetadataLockedHintAgentMcp',
    }
  }
  return {
    label: 'publish.fieldPluginIdAgentPlugin',
    hint: 'publish.fieldPluginIdHelpAgentPlugin',
    newOption: 'publish.pluginIdNewOptionAgentPlugin',
    metadataLocked: 'publish.fieldMetadataLockedHintAgentPlugin',
  }
}

export function resolvePublishFormFieldLabels(type: PublishDrawerType, t: TFunction): PublishFormFieldLabels {
  if (isAgentAssetPluginType(type)) {
    const linkKeys = agentPluginLinkKeys(type)
    const pkgNameKey =
      type === 'agent-template'
        ? 'publish.fieldPkgNameAgentTemplate'
        : type === 'agent-mcp'
          ? 'publish.fieldPkgNameAgentMcp'
          : 'publish.fieldPkgNameAgentPlugin'
    const placeholderKey =
      type === 'agent-template'
        ? 'publish.namePlaceholderAgentTemplate'
        : type === 'agent-mcp'
          ? 'publish.namePlaceholderAgentMcp'
          : 'publish.namePlaceholderAgentPlugin'
    return {
      pkgName: t(pkgNameKey),
      pkgNameHelp: t('publish.fieldPkgNameAgentHelp'),
      pkgNamePlaceholder: t(placeholderKey),
      versionHelp: t('publish.fieldVersionAgentHelp'),
      displayName: t('publish.fieldDisplayName'),
      displayNameHelp: t('publish.fieldDisplayNameAgentHelp'),
      description: t('publish.fieldDescription'),
      descriptionHelp: t('publish.fieldDescriptionAgentHelp'),
      descriptionPlaceholder: t('publish.fieldDescriptionAgentPlaceholder'),
      tags: t('publish.fieldTags'),
      tagsHelp: t('publish.fieldTagsAgentHelp'),
      folder: t('publish.fieldAgentZip'),
      folderHelp: t('publish.fieldAgentZipHelp'),
      icon: t('publish.fieldSkillIcon'),
      iconHelp: t('publish.fieldSkillIconHelp'),
      metadataLockedHint: t(linkKeys.metadataLocked),
      pluginLinkLabel: t(linkKeys.label),
      pluginLinkHint: t(linkKeys.hint),
      pluginNewOptionLabel: t(linkKeys.newOption),
    }
  }

  if (type === 'swarmskill') {
    return {
      pkgName: t('publish.fieldPkgNameSwarmSkill'),
      pkgNameHelp: t('publish.fieldPkgNameSwarmSkillHelp'),
      pkgNamePlaceholder: t('publish.namePlaceholderSwarmSkill'),
      versionHelp: t('publish.fieldVersionSwarmSkillHelp'),
      displayName: t('publish.fieldDisplayName'),
      displayNameHelp: t('publish.fieldDisplayNameSwarmSkillHelp'),
      description: t('publish.fieldDescription'),
      descriptionHelp: t('publish.fieldSkillDescriptionSwarmSkillHelp'),
      descriptionPlaceholder: t('publish.fieldSkillDescriptionPlaceholder'),
      tags: t('publish.fieldTags'),
      tagsHelp: t('publish.fieldTagsSwarmSkillHelp'),
      folder: t('publish.fieldSkillFolderSwarmSkill'),
      folderHelp: t('publish.fieldSkillFolderSwarmSkillHelp'),
      icon: t('publish.fieldSkillIconSwarmSkill'),
      iconHelp: t('publish.fieldSkillIconSwarmSkillHelp'),
      metadataLockedHint: t('publish.fieldMetadataLockedHintSwarmSkill'),
      pluginLinkLabel: t('publish.fieldPluginIdSwarmSkill'),
      pluginLinkHint: t('publish.fieldPluginIdHelpSwarmSkill'),
      pluginNewOptionLabel: t('publish.pluginIdNewOptionSwarmSkill'),
    }
  }

  return {
    pkgName: t('publish.fieldSkillName'),
    pkgNameHelp: t('publish.fieldSkillNameHelp'),
    pkgNamePlaceholder: t('publish.namePlaceholderSkill'),
    versionHelp: t('publish.fieldVersionSkillHelp'),
    displayName: t('publish.fieldDisplayName'),
    displayNameHelp: t('publish.fieldSkillDisplayNameHelp'),
    description: t('publish.fieldDescription'),
    descriptionHelp: t('publish.fieldSkillDescriptionHelp'),
    descriptionPlaceholder: t('publish.fieldSkillDescriptionPlaceholder'),
    tags: t('publish.fieldTags'),
    tagsHelp: t('publish.fieldSkillTagsHelp'),
    folder: t('publish.fieldSkillFolder'),
    folderHelp: t('publish.fieldSkillFolderHelp'),
    icon: t('publish.fieldSkillIcon'),
    iconHelp: t('publish.fieldSkillIconHelp'),
    metadataLockedHint: t('publish.fieldSkillMetadataLockedHint'),
    pluginLinkLabel: t('publish.fieldPluginIdSkill'),
    pluginLinkHint: t('publish.fieldPluginIdHelpSkill'),
    pluginNewOptionLabel: t('publish.pluginIdNewOptionSkill'),
  }
}
