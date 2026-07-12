import { Alert, Button, Card, Col, Row, Space, Spin, Tag, Typography, message, Select } from 'antd'
import { useEffect, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import api, { Instance, getErrorMessage } from '../api/client'
import dayjs from 'dayjs'

export default function InstanceDetailPage() {
  const { region = '', name = '' } = useParams()
  const [search] = useSearchParams()
  const credentialId = search.get('credential_id') || undefined
  const [inst, setInst] = useState<Instance | null>(null)
  const [loading, setLoading] = useState(true)
  const [period, setPeriod] = useState<'day' | 'week' | 'month'>('day')
  const [points, setPoints] = useState<{ time: string; in_mb: number; out_mb: number }[]>([])

  const params = credentialId ? { credential_id: credentialId } : {}

  const load = async () => {
    setLoading(true)
    try {
      const res = await api.get<Instance>(`/instances/${region}/${name}`, { params })
      setInst(res.data)
    } catch (err) {
      message.error(getErrorMessage(err))
    } finally {
      setLoading(false)
    }
  }

  const loadMetrics = async (p: 'day' | 'week' | 'month') => {
    try {
      const res = await api.get(`/metrics/${region}/${name}`, {
        params: { period: p, ...params },
      })
      setPoints(
        (res.data.points || []).map(
          (pt: { timestamp: string; network_in_bytes: number; network_out_bytes: number }) => ({
            time: dayjs(pt.timestamp).format(p === 'day' ? 'HH:mm' : 'MM-DD HH:mm'),
            in_mb: Number((pt.network_in_bytes / (1024 * 1024)).toFixed(2)),
            out_mb: Number((pt.network_out_bytes / (1024 * 1024)).toFixed(2)),
          }),
        ),
      )
    } catch (err) {
      message.error(getErrorMessage(err))
    }
  }

  useEffect(() => {
    void load()
  }, [region, name, credentialId])

  useEffect(() => {
    void loadMetrics(period)
  }, [region, name, period, credentialId])

  if (loading && !inst) return <Spin />

  if (!inst) {
    return <Alert type="error" message="实例不存在或加载失败" />
  }

  return (
    <Space direction="vertical" size="large" style={{ width: '100%' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between' }}>
        <div>
          <Typography.Title level={3} style={{ margin: 0 }}>
            {inst.name}
          </Typography.Title>
          <Typography.Text type="secondary">
            {inst.account_label ? `[${inst.account_label}] ` : ''}
            {inst.region} · <Tag>{inst.state}</Tag>
          </Typography.Text>
        </div>
        <Space>
          <Button>
            <Link to="/instances">返回列表</Link>
          </Button>
          <Button onClick={() => void load()}>刷新</Button>
        </Space>
      </div>

      <Row gutter={[16, 16]}>
        <Col xs={24} md={8}>
          <Card title="基本信息">
            <p>AWS 凭证：{inst.account_label || (inst.credential_id ? `#${inst.credential_id}` : '-')}</p>
            <p>公网 IP：{inst.public_ip || '-'}</p>
            <p>内网 IP：{inst.private_ip || '-'}</p>
            <p>静态 IP：{inst.is_static_ip ? inst.static_ip_name || '是' : '否'}</p>
            <p>镜像：{inst.blueprint_name || inst.blueprint_id || '-'}</p>
            <p>套餐：{inst.bundle_id || '-'}</p>
            <p>
              当月流量：{inst.traffic?.total_gb ?? 0} GB
              {inst.traffic?.over_limit ? (
                <Tag color="red" style={{ marginLeft: 8 }}>
                  超限
                </Tag>
              ) : null}
            </p>
            <p>月限额：{inst.monthly_limit_gb ?? inst.traffic?.limit_gb ?? '未设置'} GB</p>
            <p>
              超限自动关机：
              {inst.auto_stop_on_limit ? (
                <Tag color="orange">已开启</Tag>
              ) : (
                <Tag>仅告警</Tag>
              )}
            </p>
            <p>备注：{inst.note || '-'}</p>
          </Card>
        </Col>
        <Col xs={24} md={16}>
          <Card
            title="流量趋势（估算）"
            extra={
              <Select
                value={period}
                style={{ width: 120 }}
                onChange={(v) => setPeriod(v)}
                options={[
                  { value: 'day', label: '近 24 小时' },
                  { value: 'week', label: '近 7 天' },
                  { value: 'month', label: '近 30 天' },
                ]}
              />
            }
          >
            <div style={{ width: '100%', height: 320 }}>
              <ResponsiveContainer>
                <LineChart data={points}>
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis dataKey="time" minTickGap={24} />
                  <YAxis unit=" MB" />
                  <Tooltip />
                  <Legend />
                  <Line type="monotone" dataKey="in_mb" name="入站 MB" stroke="#1677ff" dot={false} />
                  <Line type="monotone" dataKey="out_mb" name="出站 MB" stroke="#52c41a" dot={false} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </Card>
        </Col>
      </Row>
    </Space>
  )
}
