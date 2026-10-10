// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import axios from 'axios'
import type { AxiosInstance } from 'axios'
import { clearOAuthSession, getStoredOAuthProvider, getStoredOAuthToken } from '@/auth/gitcodeStorage'
import { getAppAbsoluteUrl } from '@/utils/appBasePath'
import { API_CONFIG } from './config'

let apiClient: AxiosInstance | null = null

export function getApiClient(): AxiosInstance {
  if (!apiClient) {
    apiClient = axios.create({
      baseURL: API_CONFIG.BASE_URL,
      timeout: API_CONFIG.TIMEOUT,
      headers: API_CONFIG.HEADERS,
    })

    // Request: attach stored OAuth token
    apiClient.interceptors.request.use(config => {
      const t = getStoredOAuthToken()
      if (t) {
        config.headers.Authorization = `Bearer ${t}`
        config.headers['X-OAuth-Provider'] = getStoredOAuthProvider()
      } else {
        delete config.headers.Authorization
        delete config.headers['X-OAuth-Provider']
      }
      return config
    })

    // Response: on 401, clear session and redirect to login
    apiClient.interceptors.response.use(
      response => response,
      error => {
        if (error.response?.status === 401) {
          clearOAuthSession()
          // Avoid redirect loops — only redirect if not already on login page
          if (!window.location.pathname.includes('/login')) {
            window.location.href = getAppAbsoluteUrl('/login')
          }
        }
        return Promise.reject(error)
      },
    )
  }
  return apiClient
}
