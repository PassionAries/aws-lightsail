import axios, { AxiosError } from 'axios'

const api = axios.create({
  baseURL: '/api',
  timeout: 120000,
})

api.interceptors.request.use((config) => {
  const token = localStorage.getItem('token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

api.interceptors.response.use(
  (res) => res,
  (error: AxiosError<{ detail?: string | { msg: string }[] }>) => {
    if (error.response?.status === 401) {
      const path = window.location.pathname
      if (path !== '/login') {
        localStorage.removeItem('token')
        localStorage.removeItem('user')
        window.location.href = '/login'
      }
    }
    return Promise.reject(error)
  },
)

export function getErrorMessage(err: unknown, fallback = '请求失败'): string {
  if (axios.isAxiosError(err)) {
    const detail = err.response?.data?.detail
    if (typeof detail === 'string') return detail
    if (Array.isArray(detail)) return detail.map((d) => d.msg).join('; ')
    return err.message || fallback
  }
  if (err instanceof Error) return err.message
  return fallback
}

export interface User {
  id: number
  username: string
  is_admin: boolean
  monthly_limit_gb: number | null
  auto_stop_on_limit_default?: boolean
  created_at: string
  has_credentials: boolean
  credential_count?: number
}

export interface CredentialItem {
  id: number
  access_key_masked: string
  account_label?: string | null
  is_default: boolean
  last_validated_at?: string | null
  created_at?: string | null
  /** Lightsail 每 Region 最大 vCPU，如 20 */
  vcpu_quota?: number | null
  /** 社区档位标签：5V / 8V / 32V */
  vcpu_tier?: string | null
  static_ip_quota?: number | null
  used_vcpu?: number | null
  used_instance_count?: number | null
  remaining_vcpu?: number | null
  quota_region?: string | null
  quota_message?: string | null
  quota_checked_at?: string | null
}

export interface CredentialOut {
  has_credentials: boolean
  items: CredentialItem[]
  id?: number | null
  access_key_masked?: string | null
  account_label?: string | null
  last_validated_at?: string | null
}

export interface InstanceTraffic {
  in_gb: number
  out_gb: number
  total_gb: number
  limit_gb: number | null
  over_limit: boolean
  year_month?: string | null
}

export interface Instance {
  name: string
  region: string
  availability_zone?: string | null
  state: string
  public_ip?: string | null
  private_ip?: string | null
  blueprint_id?: string | null
  blueprint_name?: string | null
  bundle_id?: string | null
  is_static_ip: boolean
  static_ip_name?: string | null
  created_at?: string | null
  traffic?: InstanceTraffic | null
  monthly_limit_gb?: number | null
  auto_stop_on_limit?: boolean
  note?: string | null
  credential_id?: number | null
  account_label?: string | null
}

export interface TrafficSummary {
  year_month: string
  instances: {
    region: string
    name: string
    credential_id?: number | null
    account_label?: string | null
    in_gb: number
    out_gb: number
    total_gb: number
    limit_gb: number | null
    over_limit: boolean
    auto_stop_on_limit?: boolean
    year_month: string
  }[]
  by_region: {
    region: string
    total_gb: number
    instance_count: number
  }[]
  note: string
}

export default api
