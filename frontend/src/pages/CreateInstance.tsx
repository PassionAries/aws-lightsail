import { Alert, Button, Card, Form, Input, Select, Switch, Typography, message, Space } from 'antd'
import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import api, { CredentialItem, CredentialOut, getErrorMessage } from '../api/client'
import { useAuth } from '../auth/AuthContext'

interface Region {
  name: string
  display_name: string
}
interface Bundle {
  bundle_id: string
  name: string
  price?: number
  cpu_count?: number
  ram_size_in_gb?: number
  transfer_per_month_in_gb?: number
  is_active: boolean
}
interface Blueprint {
  blueprint_id: string
  name: string
  platform?: string
  group?: string
  type?: string
  version?: string
  is_active: boolean
}

/** 镜像下拉文案：必须带上版本，否则多个 Ubuntu/Debian 会显示成同一项 */
function blueprintLabel(b: Blueprint): string {
  const parts: string[] = [b.name || b.blueprint_id]
  if (b.version) parts.push(b.version)
  if (b.type && b.type !== 'os') parts.push(b.type)
  if (b.group && b.group !== b.name) parts.push(b.group)
  // 平台对同类 OS 往往相同，放最后作参考
  if (b.platform) parts.push(b.platform)
  // 同名同版本时用 blueprint_id 区分（极少数情况）
  const base = parts.join(' · ')
  return `${base}  (${b.blueprint_id})`
}

export default function CreateInstancePage() {
  const { user } = useAuth()
  const [form] = Form.useForm()
  const [creds, setCreds] = useState<CredentialItem[]>([])
  const [regions, setRegions] = useState<Region[]>([])
  const [bundles, setBundles] = useState<Bundle[]>([])
  const [blueprints, setBlueprints] = useState<Blueprint[]>([])
  const [loadingMeta, setLoadingMeta] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const navigate = useNavigate()

  useEffect(() => {
    if (!user?.has_credentials) return
    void (async () => {
      try {
        const cRes = await api.get<CredentialOut>('/credentials')
        const items = cRes.data.items || []
        setCreds(items)
        const defaultId = items.find((c) => c.is_default)?.id || items[0]?.id
        if (defaultId) {
          form.setFieldsValue({ credential_id: defaultId })
          await loadRegions(defaultId)
        }
      } catch (err) {
        message.error(getErrorMessage(err))
      }
    })()
  }, [user?.has_credentials])

  const loadRegions = async (credentialId: number) => {
    try {
      const res = await api.get<Region[]>('/catalog/regions', {
        params: { credential_id: credentialId },
      })
      setRegions(res.data)
      setBundles([])
      setBlueprints([])
      form.setFieldsValue({ region: undefined, blueprint_id: undefined, bundle_id: undefined })
    } catch (err) {
      message.error(getErrorMessage(err))
    }
  }

  const loadCatalog = async (region: string, credentialId: number) => {
    setLoadingMeta(true)
    form.setFieldsValue({ blueprint_id: undefined, bundle_id: undefined })
    try {
      const [bRes, pRes] = await Promise.all([
        api.get<Bundle[]>('/catalog/bundles', { params: { region, credential_id: credentialId } }),
        api.get<Blueprint[]>('/catalog/blueprints', {
          params: { region, credential_id: credentialId },
        }),
      ])
      setBundles(bRes.data.filter((b) => b.is_active !== false))
      const bps = pRes.data.filter((b) => b.is_active !== false)
      bps.sort((a, c) => {
        // OS 优先，再按名称+版本
        const score = (x: Blueprint) => (x.type === 'os' ? 0 : 1)
        const s = score(a) - score(c)
        if (s !== 0) return s
        const na = `${a.name || ''} ${a.version || ''}`.toLowerCase()
        const nc = `${c.name || ''} ${c.version || ''}`.toLowerCase()
        return na.localeCompare(nc)
      })
      setBlueprints(bps)
    } catch (err) {
      message.error(getErrorMessage(err))
    } finally {
      setLoadingMeta(false)
    }
  }

  if (!user?.has_credentials) {
    return (
      <Alert
        type="info"
        showIcon
        message="请先绑定 AWS 凭证"
        description={<Link to="/credentials">前往绑定</Link>}
      />
    )
  }

  return (
    <Space direction="vertical" size="large" style={{ width: '100%', maxWidth: 720 }}>
      <Typography.Title level={3} style={{ margin: 0 }}>
        开通实例
      </Typography.Title>
      <Card>
        <Form
          form={form}
          layout="vertical"
          initialValues={{ allocate_static_ip: true }}
          onFinish={async (values) => {
            setSubmitting(true)
            try {
              const res = await api.post('/instances', values, { timeout: 300000 })
              message.success(res.data.message || '创建请求已提交')
              navigate('/instances')
            } catch (err) {
              message.error(getErrorMessage(err))
            } finally {
              setSubmitting(false)
            }
          }}
        >
          <Form.Item
            name="credential_id"
            label="使用的 AWS 凭证"
            rules={[{ required: true, message: '请选择凭证' }]}
          >
            <Select
              options={creds.map((c) => ({
                value: c.id,
                label: `${c.account_label || '凭证'} #${c.id}${c.is_default ? ' (默认)' : ''} · ${c.access_key_masked}`,
              }))}
              onChange={(v) => void loadRegions(v)}
            />
          </Form.Item>

          <Form.Item name="region" label="地区" rules={[{ required: true, message: '请选择地区' }]}>
            <Select
              showSearch
              optionFilterProp="label"
              placeholder="选择 Lightsail 地区"
              options={regions.map((r) => ({
                value: r.name,
                label: `${r.display_name} (${r.name})`,
              }))}
              onChange={(v) => {
                const cid = form.getFieldValue('credential_id')
                if (cid) void loadCatalog(v, cid)
              }}
            />
          </Form.Item>

          <Form.Item
            name="instance_name"
            label="实例名称"
            rules={[
              { required: true, message: '请输入名称' },
              {
                pattern: /^[a-zA-Z0-9][a-zA-Z0-9-]*$/,
                message: '仅字母数字和连字符，且以字母或数字开头',
              },
            ]}
          >
            <Input placeholder="my-instance" />
          </Form.Item>

          <Form.Item
            name="blueprint_id"
            label="系统镜像"
            rules={[{ required: true, message: '请选择镜像' }]}
          >
            <Select
              showSearch
              loading={loadingMeta}
              optionFilterProp="label"
              placeholder="Ubuntu / Debian / Windows ..."
              options={blueprints.map((b) => ({
                value: b.blueprint_id,
                label: blueprintLabel(b),
              }))}
            />
          </Form.Item>

          <Form.Item name="bundle_id" label="套餐" rules={[{ required: true, message: '请选择套餐' }]}>
            <Select
              showSearch
              loading={loadingMeta}
              optionFilterProp="label"
              placeholder="nano / micro / small ..."
              options={bundles.map((b) => ({
                value: b.bundle_id,
                label: `${b.name} · $${b.price ?? '?'}/月 · ${b.cpu_count ?? '?'} vCPU · ${
                  b.ram_size_in_gb ?? '?'
                } GB · 流量 ${b.transfer_per_month_in_gb ?? '?'} GB`,
              }))}
            />
          </Form.Item>

          <Form.Item
            name="allocate_static_ip"
            label="自动分配并绑定静态 IP"
            valuePropName="checked"
            extra="建议开启。一键换 IP 与删除清理均基于静态 IP。"
          >
            <Switch />
          </Form.Item>

          <Button type="primary" htmlType="submit" loading={submitting} block>
            创建
          </Button>
        </Form>
      </Card>
    </Space>
  )
}
