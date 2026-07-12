import { Alert, Button, Form, Input, Typography, Card, message } from 'antd'
import { useState } from 'react'
import { Navigate, useNavigate } from 'react-router-dom'
import { getErrorMessage, useAuth } from '../auth/AuthContext'

export default function LoginPage() {
  const { login, token, loading } = useAuth()
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const navigate = useNavigate()

  if (!loading && token) return <Navigate to="/" replace />

  const onFinish = async (values: { username: string; password: string }) => {
    setSubmitting(true)
    setError(null)
    try {
      await login(values.username, values.password)
      message.success('登录成功')
      navigate('/')
    } catch (err) {
      setError(getErrorMessage(err, '登录失败'))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div
      style={{
        minHeight: '100vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: 'linear-gradient(135deg, #0f172a 0%, #1e3a5f 50%, #0ea5e9 100%)',
        padding: 24,
      }}
    >
      <Card style={{ width: 400, boxShadow: '0 12px 40px rgba(0,0,0,.25)' }}>
        <Typography.Title level={3} style={{ textAlign: 'center', marginBottom: 8 }}>
          Lightsail 管理平台
        </Typography.Title>
        <Typography.Paragraph type="secondary" style={{ textAlign: 'center' }}>
          登录后绑定 AWS Key 管理实例
        </Typography.Paragraph>
        {error && <Alert type="error" showIcon message={error} style={{ marginBottom: 16 }} />}
        <Form layout="vertical" onFinish={onFinish} initialValues={{ username: 'admin' }}>
          <Form.Item name="username" label="用户名" rules={[{ required: true, message: '请输入用户名' }]}>
            <Input autoFocus placeholder="admin" />
          </Form.Item>
          <Form.Item name="password" label="密码" rules={[{ required: true, message: '请输入密码' }]}>
            <Input.Password placeholder="密码" />
          </Form.Item>
          <Button type="primary" htmlType="submit" block loading={submitting}>
            登录
          </Button>
        </Form>
      </Card>
    </div>
  )
}
