import { Alert, Button, Card, Col, Row, Space, Table, Tag, Typography, message, Statistic, Select } from 'antd'
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import api, { CredentialItem, CredentialOut, TrafficSummary, getErrorMessage } from '../api/client'
import { useAuth } from '../auth/AuthContext'

export default function TrafficPage() {
  const { user } = useAuth()
  const [data, setData] = useState<TrafficSummary | null>(null)
  const [creds, setCreds] = useState<CredentialItem[]>([])
  const [filterCred, setFilterCred] = useState<number | 'all'>('all')
  const [loading, setLoading] = useState(false)
  const [syncing, setSyncing] = useState(false)

  const load = async () => {
    setLoading(true)
    try {
      const params = filterCred === 'all' ? {} : { credential_id: filterCred }
      const res = await api.get<TrafficSummary>('/traffic/summary', { params })
      setData(res.data)
    } catch (err) {
      message.error(getErrorMessage(err))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (user?.has_credentials) {
      void (async () => {
        try {
          const res = await api.get<CredentialOut>('/credentials')
          setCreds(res.data.items || [])
        } catch {
          /* ignore */
        }
      })()
      void load()
    }
  }, [user?.has_credentials, filterCred])

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

  const totalAll = (data?.by_region || []).reduce((s, r) => s + r.total_gb, 0)
  const autoStopCount = (data?.instances || []).filter((i) => i.over_limit && i.auto_stop_on_limit).length

  return (
    <Space direction="vertical" size="large" style={{ width: '100%' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 12 }}>
        <Typography.Title level={3} style={{ margin: 0 }}>
          流量监控
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
                label: `${c.account_label || '凭证'} #${c.id}`,
              })),
            ]}
          />
          <Button
            type="primary"
            loading={syncing}
            onClick={async () => {
              setSyncing(true)
              try {
                const res = await api.post('/traffic/sync')
                message.success(res.data.message || '同步完成')
                await load()
              } catch (err) {
                message.error(getErrorMessage(err))
              } finally {
                setSyncing(false)
              }
            }}
          >
            立即同步
          </Button>
          <Button onClick={() => void load()} loading={loading}>
            刷新
          </Button>
        </Space>
      </div>

      <Alert
        type="warning"
        showIcon
        message={data?.note || '流量基于 Lightsail 指标估算，与账单可能存在差异'}
        description={
          <>
            统计月份（UTC）：{data?.year_month || '-'}。默认仅告警；仅当实例勾选「超限自动关机」时才会 stop。
            {autoStopCount > 0 ? ` 当前有 ${autoStopCount} 台超限且已开启自动关机。` : ''}
          </>
        }
      />

      <Row gutter={[16, 16]}>
        <Col xs={24} sm={8}>
          <Card>
            <Statistic title="全部地区总流量" value={Number(totalAll.toFixed(2))} suffix="GB" />
          </Card>
        </Col>
        <Col xs={24} sm={8}>
          <Card>
            <Statistic title="实例数（有记录）" value={data?.instances.length || 0} />
          </Card>
        </Col>
        <Col xs={24} sm={8}>
          <Card>
            <Statistic
              title="超限实例"
              value={(data?.instances || []).filter((i) => i.over_limit).length}
              valueStyle={{
                color: (data?.instances || []).some((i) => i.over_limit) ? '#cf1322' : undefined,
              }}
            />
          </Card>
        </Col>
      </Row>

      <Card title="按地区汇总（同区多机相加）">
        <Table
          rowKey="region"
          loading={loading}
          dataSource={data?.by_region || []}
          pagination={false}
          columns={[
            { title: '地区', dataIndex: 'region' },
            { title: '实例数', dataIndex: 'instance_count', width: 100 },
            {
              title: '总流量 (GB)',
              dataIndex: 'total_gb',
              render: (v: number) => <b>{v}</b>,
            },
          ]}
        />
      </Card>

      <Card title="按实例明细">
        <Table
          rowKey={(r) => `${r.credential_id}/${r.region}/${r.name}`}
          loading={loading}
          dataSource={data?.instances || []}
          columns={[
            {
              title: '账号',
              width: 120,
              render: (_, r) => r.account_label || (r.credential_id ? `#${r.credential_id}` : '-'),
            },
            { title: '地区', dataIndex: 'region', width: 130 },
            { title: '实例', dataIndex: 'name' },
            { title: '入站 GB', dataIndex: 'in_gb', width: 90 },
            { title: '出站 GB', dataIndex: 'out_gb', width: 90 },
            { title: '合计 GB', dataIndex: 'total_gb', width: 90 },
            {
              title: '限额 GB',
              dataIndex: 'limit_gb',
              width: 90,
              render: (v) => (v == null ? '-' : v),
            },
            {
              title: '超限',
              dataIndex: 'over_limit',
              width: 80,
              render: (v: boolean) =>
                v ? <Tag color="red">超限</Tag> : <Tag color="green">正常</Tag>,
            },
            {
              title: '自动关机',
              dataIndex: 'auto_stop_on_limit',
              width: 90,
              render: (v: boolean) => (v ? <Tag color="orange">开</Tag> : <Tag>关</Tag>),
            },
          ]}
        />
      </Card>
    </Space>
  )
}
