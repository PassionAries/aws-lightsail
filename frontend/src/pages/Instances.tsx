import {
  Alert,
  Button,
  Form,
  Input,
  InputNumber,
  Popconfirm,
  Space,
  Table,
  Tag,
  Typography,
  message,
  Modal,
  Card,
  Select,
  Switch,
} from 'antd'
import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import api, { CredentialItem, CredentialOut, Instance, getErrorMessage } from '../api/client'
import { useAuth } from '../auth/AuthContext'

const stateColor: Record<string, string> = {
  running: 'green',
  stopped: 'default',
  pending: 'processing',
  stopping: 'orange',
  starting: 'processing',
}

export default function InstancesPage() {
  const { user } = useAuth()
  const navigate = useNavigate()
  const [data, setData] = useState<Instance[]>([])
  const [creds, setCreds] = useState<CredentialItem[]>([])
  const [filterCred, setFilterCred] = useState<number | 'all'>('all')
  const [loading, setLoading] = useState(false)
  const [acting, setActing] = useState<string | null>(null)
  const [limitModal, setLimitModal] = useState<Instance | null>(null)
  const [limitForm] = Form.useForm()

  const loadCreds = async () => {
    try {
      const res = await api.get<CredentialOut>('/credentials')
      setCreds(res.data.items || [])
    } catch {
      /* ignore */
    }
  }

  const load = async () => {
    setLoading(true)
    try {
      const params =
        filterCred === 'all' ? {} : { credential_id: filterCred }
      const res = await api.get<Instance[]>('/instances', { params })
      setData(res.data)
    } catch (err) {
      message.error(getErrorMessage(err))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (user?.has_credentials) {
      void loadCreds()
      void load()
    }
  }, [user?.has_credentials, filterCred])

  const runAction = async (inst: Instance, action: string) => {
    const key = `${inst.credential_id}/${inst.region}/${inst.name}:${action}`
    setActing(key)
    try {
      const q = { credential_id: inst.credential_id || undefined }
      if (action === 'delete') {
        const res = await api.delete(`/instances/${inst.region}/${inst.name}`, { params: q })
        message.success(res.data.message || '已删除')
      } else if (action === 'change-ip') {
        const res = await api.post(`/instances/${inst.region}/${inst.name}/change-ip`, null, {
          params: q,
        })
        message.success(
          `换 IP 成功：${res.data.old_ip || '-'} → ${res.data.new_ip || res.data.static_ip_name}`,
        )
      } else {
        const res = await api.post(`/instances/${inst.region}/${inst.name}/${action}`, null, {
          params: q,
        })
        message.success(res.data.message || '操作已提交')
      }
      setTimeout(() => void load(), 1500)
    } catch (err) {
      message.error(getErrorMessage(err))
    } finally {
      setActing(null)
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
    <Space direction="vertical" size="large" style={{ width: '100%' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', flexWrap: 'wrap', gap: 12 }}>
        <Typography.Title level={3} style={{ margin: 0 }}>
          实例列表
        </Typography.Title>
        <Space wrap>
          <Select
            style={{ minWidth: 200 }}
            value={filterCred}
            onChange={setFilterCred}
            options={[
              { value: 'all', label: '全部凭证' },
              ...creds.map((c) => ({
                value: c.id,
                label: `${c.account_label || '凭证'} #${c.id}${c.is_default ? ' (默认)' : ''}`,
              })),
            ]}
          />
          <Button type="primary" onClick={() => navigate('/create')}>
            开通实例
          </Button>
          <Button onClick={() => void load()} loading={loading}>
            刷新
          </Button>
        </Space>
      </div>

      <Card>
        <Table
          rowKey={(r) => `${r.credential_id}/${r.region}/${r.name}`}
          loading={loading}
          dataSource={data}
          scroll={{ x: 1200 }}
          columns={[
            {
              title: '名称',
              dataIndex: 'name',
              render: (name: string, row) => (
                <Link to={`/instances/${row.region}/${name}?credential_id=${row.credential_id || ''}`}>
                  {name}
                </Link>
              ),
            },
            {
              title: 'AWS 账号',
              width: 140,
              render: (_, row) => row.account_label || (row.credential_id ? `#${row.credential_id}` : '-'),
            },
            { title: '地区', dataIndex: 'region', width: 140 },
            {
              title: '状态',
              dataIndex: 'state',
              width: 110,
              render: (s: string) => <Tag color={stateColor[s] || 'default'}>{s}</Tag>,
            },
            {
              title: '公网 IP',
              dataIndex: 'public_ip',
              width: 150,
              render: (ip: string, row) => (
                <span>
                  {ip || '-'}
                  {row.is_static_ip ? <Tag style={{ marginLeft: 6 }}>静态</Tag> : null}
                </span>
              ),
            },
            {
              title: '当月流量',
              width: 130,
              render: (_, row) => {
                const t = row.traffic
                if (!t) return '-'
                return (
                  <span style={{ color: t.over_limit ? '#cf1322' : undefined }}>
                    {t.total_gb} GB
                    {t.over_limit ? ' 超限' : ''}
                  </span>
                )
              },
            },
            {
              title: '超限关机',
              width: 90,
              render: (_, row) =>
                row.auto_stop_on_limit ? <Tag color="orange">开</Tag> : <Tag>关</Tag>,
            },
            {
              title: '操作',
              width: 340,
              fixed: 'right',
              render: (_, row) => {
                const busy = (action: string) =>
                  acting === `${row.credential_id}/${row.region}/${row.name}:${action}`
                return (
                  <Space wrap size="small">
                    <Button
                      size="small"
                      disabled={row.state === 'running'}
                      loading={busy('start')}
                      onClick={() => void runAction(row, 'start')}
                    >
                      开机
                    </Button>
                    <Button
                      size="small"
                      disabled={row.state === 'stopped'}
                      loading={busy('stop')}
                      onClick={() => void runAction(row, 'stop')}
                    >
                      关机
                    </Button>
                    <Popconfirm
                      title="确认一键更换静态 IP？"
                      description="将分配新静态 IP，并释放旧静态 IP。"
                      onConfirm={() => void runAction(row, 'change-ip')}
                    >
                      <Button size="small" loading={busy('change-ip')}>
                        换 IP
                      </Button>
                    </Popconfirm>
                    <Button
                      size="small"
                      onClick={() => {
                        setLimitModal(row)
                        limitForm.setFieldsValue({
                          monthly_limit_gb: row.monthly_limit_gb,
                          auto_stop_on_limit: !!row.auto_stop_on_limit,
                          note: row.note,
                        })
                      }}
                    >
                      限额
                    </Button>
                    <Popconfirm
                      title="确认删除实例？"
                      description="将删除实例并释放关联静态 IP，不可恢复。"
                      onConfirm={() => void runAction(row, 'delete')}
                    >
                      <Button size="small" danger loading={busy('delete')}>
                        删除
                      </Button>
                    </Popconfirm>
                  </Space>
                )
              },
            },
          ]}
        />
      </Card>

      <Modal
        title={limitModal ? `限额与超限策略 - ${limitModal.name}` : '设置'}
        open={!!limitModal}
        onCancel={() => setLimitModal(null)}
        onOk={async () => {
          if (!limitModal) return
          const values = await limitForm.validateFields()
          try {
            await api.patch(
              `/instances/${limitModal.region}/${limitModal.name}/settings`,
              values,
              { params: { credential_id: limitModal.credential_id || undefined } },
            )
            message.success('已保存')
            setLimitModal(null)
            void load()
          } catch (err) {
            message.error(getErrorMessage(err))
          }
        }}
      >
        <Form form={limitForm} layout="vertical">
          <Form.Item
            name="monthly_limit_gb"
            label="月流量限额（GB）"
            tooltip="基于 NetworkIn+Out 估算。清空表示不单独限制。"
          >
            <InputNumber min={0} step={1} style={{ width: '100%' }} placeholder="例如 1024" />
          </Form.Item>
          <Form.Item
            name="auto_stop_on_limit"
            label="超限后自动关机"
            valuePropName="checked"
            extra="默认关闭：仅告警。勾选后，采集任务发现超限且实例运行中时会自动 stop。"
          >
            <Switch checkedChildren="开启" unCheckedChildren="仅告警" />
          </Form.Item>
          <Form.Item name="note" label="备注">
            <Input.TextArea rows={2} />
          </Form.Item>
        </Form>
      </Modal>
    </Space>
  )
}
