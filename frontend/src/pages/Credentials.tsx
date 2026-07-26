import {
  Alert,
  Button,
  Card,
  Form,
  Input,
  Popconfirm,
  Space,
  Switch,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd'
import { useEffect, useState } from 'react'
import api, { CredentialItem, CredentialOut, getErrorMessage } from '../api/client'
import { useAuth } from '../auth/AuthContext'
import dayjs from 'dayjs'

function tierColor(tier?: string | null): string {
  if (!tier) return 'default'
  const n = parseFloat(tier)
  if (Number.isNaN(n)) return 'blue'
  if (n >= 32) return 'purple'
  if (n >= 20) return 'geekblue'
  if (n >= 8) return 'green'
  if (n >= 5) return 'cyan'
  return 'orange'
}

export default function CredentialsPage() {
  const { refreshMe } = useAuth()
  const [items, setItems] = useState<CredentialItem[]>([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [quotaLoadingId, setQuotaLoadingId] = useState<number | null>(null)
  const [form] = Form.useForm()

  const load = async () => {
    setLoading(true)
    try {
      const res = await api.get<CredentialOut>('/credentials')
      setItems(res.data.items || [])
    } catch (err) {
      message.error(getErrorMessage(err))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void load()
  }, [])

  const refreshQuota = async (id: number) => {
    setQuotaLoadingId(id)
    try {
      const res = await api.post<CredentialItem>(`/credentials/${id}/quotas`)
      setItems((prev) => prev.map((x) => (x.id === id ? { ...x, ...res.data } : x)))
      if (res.data.vcpu_tier) {
        message.success(`配额：${res.data.vcpu_tier}${res.data.vcpu_quota != null ? `（${res.data.vcpu_quota} vCPU/Region）` : ''}`)
      } else {
        message.warning(res.data.quota_message || '未能读取 vCPU 配额')
      }
    } catch (err) {
      message.error(getErrorMessage(err))
    } finally {
      setQuotaLoadingId(null)
    }
  }

  return (
    <Space direction="vertical" size="large" style={{ width: '100%' }}>
      <Typography.Title level={3} style={{ margin: 0 }}>
        AWS 凭证
      </Typography.Title>

      <Alert
        type="info"
        showIcon
        message="支持绑定多组 AWS Access Key。可检测每组账号的 Lightsail 配额（Service Quotas：Instances = 每 Region 最大 vCPU，社区常称 5V / 8V / 32V）。"
        description="配额按 Region 生效；默认查询 us-east-1。部分新号在 Service Quotas 中可能显示 Not available，需通过账单/支持工单提额。添加或「校验」时会自动刷新配额。"
      />

      <Card title={`已绑定（${items.length}）`} loading={loading}>
        <Table
          rowKey="id"
          dataSource={items}
          pagination={false}
          columns={[
            {
              title: '备注',
              dataIndex: 'account_label',
              render: (v, row) => (
                <Space>
                  {v || `凭证 #${row.id}`}
                  {row.is_default ? <Tag color="blue">默认</Tag> : null}
                </Space>
              ),
            },
            { title: 'Access Key', dataIndex: 'access_key_masked' },
            {
              title: 'Lightsail 配额',
              key: 'quota',
              render: (_, row) => {
                if (row.vcpu_tier || row.vcpu_quota != null) {
                  const used =
                    row.used_vcpu != null
                      ? `已用 ${row.used_vcpu} vCPU` +
                        (row.used_instance_count != null ? ` / ${row.used_instance_count} 台` : '')
                      : null
                  const remain =
                    row.remaining_vcpu != null ? `剩余约 ${row.remaining_vcpu} vCPU` : null
                  const tip = [
                    row.quota_region ? `查询 Region: ${row.quota_region}` : null,
                    row.vcpu_quota != null ? `Instances 配额: ${row.vcpu_quota} vCPU/Region` : null,
                    row.static_ip_quota != null ? `静态 IP 配额: ${row.static_ip_quota}` : null,
                    used,
                    remain,
                    row.quota_message,
                    row.quota_checked_at
                      ? `检测于 ${dayjs(row.quota_checked_at).format('YYYY-MM-DD HH:mm')}`
                      : null,
                  ]
                    .filter(Boolean)
                    .join('\n')
                  return (
                    <Tooltip title={<span style={{ whiteSpace: 'pre-line' }}>{tip}</span>}>
                      <Space size={4} wrap>
                        <Tag color={tierColor(row.vcpu_tier)}>{row.vcpu_tier || `${row.vcpu_quota}V`}</Tag>
                        {row.used_vcpu != null && row.vcpu_quota != null ? (
                          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                            {row.used_vcpu}/{row.vcpu_quota}
                          </Typography.Text>
                        ) : null}
                      </Space>
                    </Tooltip>
                  )
                }
                if (row.quota_message) {
                  return (
                    <Tooltip title={row.quota_message}>
                      <Tag>未知</Tag>
                    </Tooltip>
                  )
                }
                return <Typography.Text type="secondary">未检测</Typography.Text>
              },
            },
            {
              title: '最近校验',
              dataIndex: 'last_validated_at',
              render: (v) => (v ? dayjs(v).format('YYYY-MM-DD HH:mm') : '-'),
            },
            {
              title: '操作',
              render: (_, row) => (
                <Space wrap>
                  {!row.is_default && (
                    <Button
                      size="small"
                      onClick={async () => {
                        try {
                          const res = await api.post<CredentialOut>(`/credentials/${row.id}/default`)
                          setItems(res.data.items || [])
                          message.success('已设为默认')
                          await refreshMe()
                        } catch (err) {
                          message.error(getErrorMessage(err))
                        }
                      }}
                    >
                      设为默认
                    </Button>
                  )}
                  <Button
                    size="small"
                    onClick={async () => {
                      try {
                        await api.post(`/credentials/${row.id}/validate`)
                        message.success('校验成功（已同步配额）')
                        void load()
                      } catch (err) {
                        message.error(getErrorMessage(err))
                      }
                    }}
                  >
                    校验
                  </Button>
                  <Button
                    size="small"
                    type="primary"
                    ghost
                    loading={quotaLoadingId === row.id}
                    onClick={() => void refreshQuota(row.id)}
                  >
                    检测配额
                  </Button>
                  <Popconfirm
                    title="确认删除该凭证？"
                    description="相关本地限额设置与流量记录会一并删除。"
                    onConfirm={async () => {
                      try {
                        await api.delete(`/credentials/${row.id}`)
                        message.success('已删除')
                        await load()
                        await refreshMe()
                      } catch (err) {
                        message.error(getErrorMessage(err))
                      }
                    }}
                  >
                    <Button size="small" danger>
                      删除
                    </Button>
                  </Popconfirm>
                </Space>
              ),
            },
          ]}
        />
      </Card>

      <Card title="添加一组 AWS 凭证" style={{ maxWidth: 720 }}>
        <Form
          form={form}
          layout="vertical"
          initialValues={{ is_default: items.length === 0 }}
          onFinish={async (values) => {
            setSaving(true)
            try {
              const res = await api.post<CredentialOut>('/credentials', values)
              setItems(res.data.items || [])
              const latest = res.data.items?.[res.data.items.length - 1]
              const tier = latest?.vcpu_tier
              message.success(tier ? `添加成功，配额 ${tier}` : '添加成功')
              form.resetFields()
              form.setFieldsValue({ is_default: false })
              await refreshMe()
            } catch (err) {
              message.error(getErrorMessage(err))
            } finally {
              setSaving(false)
            }
          }}
        >
          <Form.Item
            name="access_key_id"
            label="Access Key ID"
            rules={[{ required: true, message: '必填' }]}
          >
            <Input placeholder="AKIA..." autoComplete="off" />
          </Form.Item>
          <Form.Item
            name="secret_access_key"
            label="Secret Access Key"
            rules={[{ required: true, message: '必填' }]}
          >
            <Input.Password placeholder="密钥" autoComplete="new-password" />
          </Form.Item>
          <Form.Item name="account_label" label="备注名">
            <Input placeholder="例如：主账号 / 美国小号 / 测试号" />
          </Form.Item>
          <Form.Item
            name="is_default"
            label="设为默认凭证"
            valuePropName="checked"
            extra="未指定凭证时的操作与 catalog 将使用默认凭证。"
          >
            <Switch />
          </Form.Item>
          <Button type="primary" htmlType="submit" loading={saving}>
            添加并校验（含配额检测）
          </Button>
        </Form>
      </Card>
    </Space>
  )
}
