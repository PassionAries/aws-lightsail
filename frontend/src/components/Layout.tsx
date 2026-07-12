import {
  CloudServerOutlined,
  DashboardOutlined,
  KeyOutlined,
  LogoutOutlined,
  PlusOutlined,
  TeamOutlined,
  LineChartOutlined,
} from '@ant-design/icons'
import { Layout, Menu, Typography, theme } from 'antd'
import { Link, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { useAuth } from '../auth/AuthContext'

const { Header, Sider, Content } = Layout

export default function AppLayout() {
  const { user, logout } = useAuth()
  const location = useLocation()
  const navigate = useNavigate()
  const { token: themeToken } = theme.useToken()

  const selected = '/' + (location.pathname.split('/')[1] || '')

  const items = [
    { key: '/', icon: <DashboardOutlined />, label: <Link to="/">总览</Link> },
    { key: '/instances', icon: <CloudServerOutlined />, label: <Link to="/instances">实例</Link> },
    { key: '/create', icon: <PlusOutlined />, label: <Link to="/create">开通实例</Link> },
    { key: '/traffic', icon: <LineChartOutlined />, label: <Link to="/traffic">流量</Link> },
    { key: '/credentials', icon: <KeyOutlined />, label: <Link to="/credentials">AWS 凭证</Link> },
    ...(user?.is_admin
      ? [{ key: '/users', icon: <TeamOutlined />, label: <Link to="/users">用户管理</Link> }]
      : []),
  ]

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Sider breakpoint="lg" collapsedWidth={64} theme="dark">
        <div style={{ color: '#fff', padding: '16px', fontWeight: 700, fontSize: 16 }}>
          Lightsail
        </div>
        <Menu theme="dark" mode="inline" selectedKeys={[selected === '/' ? '/' : selected]} items={items} />
      </Sider>
      <Layout>
        <Header
          style={{
            background: themeToken.colorBgContainer,
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            padding: '0 24px',
            borderBottom: `1px solid ${themeToken.colorBorderSecondary}`,
          }}
        >
          <Typography.Text type="secondary">AWS Lightsail 管理平台</Typography.Text>
          <div style={{ display: 'flex', gap: 16, alignItems: 'center' }}>
            <Typography.Text>
              {user?.username}
              {user?.is_admin ? '（管理员）' : ''}
            </Typography.Text>
            <a
              onClick={() => {
                logout()
                navigate('/login')
              }}
            >
              <LogoutOutlined /> 退出
            </a>
          </div>
        </Header>
        <Content style={{ margin: 24 }}>
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  )
}
