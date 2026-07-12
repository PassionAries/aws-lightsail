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
  Typography,
  message,
} from 'antd'
import { useEffect, useState } from 'react'
import api, { CredentialItem, CredentialOut, getErrorMessage } from '../api/client'
import { useAuth } from '../auth/AuthContext'
import dayjs from 'dayjs'

export default function CredentialsPage() {
  const { refreshMe } = useAuth()
  const [items, setItems] = useState<CredentialItem[]>([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
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

  return (
    <Space direction="vertical" size="large" style={{ width: '100%' }}>
      <Typography.Title level={3} style={{ margin: 0 }}>
        AWS 凭证
      </Typography.Title>

      <Alert
        type="info"
        showIcon
        message="支持绑定多组 AWS Access Key。实例列表可按凭证筛选，操作会使用对应 Key。凭证使用 ENCRYPTION_KEY 加密存储。"
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
                        message.success('校验成功')
                        void load()
                      } catch (err) {
                        message.error(getErrorMessage(err))
                      }
                    }}
                  >
                    校验
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
              message.success('添加成功')
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
            添加并校验
          </Button>
        </Form>
      </Card>
    </Space>
  )
}
