import axios from 'axios'
import i18n from '../i18n'

export const ACCESS_TOKEN_STORAGE_KEY = 'narraworld_access_token'

export const getAccessToken = () => {
  if (typeof window === 'undefined') return import.meta.env.VITE_NARRAWORLD_ACCESS_TOKEN || ''
  return window.localStorage.getItem(ACCESS_TOKEN_STORAGE_KEY) || import.meta.env.VITE_NARRAWORLD_ACCESS_TOKEN || ''
}

export const setAccessToken = (token) => {
  if (typeof window === 'undefined') return
  const cleaned = String(token || '').trim()
  if (cleaned) window.localStorage.setItem(ACCESS_TOKEN_STORAGE_KEY, cleaned)
  else window.localStorage.removeItem(ACCESS_TOKEN_STORAGE_KEY)
}

// 创建axios实例
const service = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || '',
  timeout: 300000, // 5分钟超时（本体生成可能需要较长时间）
  headers: {
    'Content-Type': 'application/json'
  }
})

// 请求拦截器
service.interceptors.request.use(
  config => {
    config.headers['Accept-Language'] = i18n.global.locale.value
    const accessToken = getAccessToken()
    if (accessToken) {
      config.headers['Authorization'] = `Bearer ${accessToken}`
      config.headers['X-NarraWorld-Token'] = accessToken
    }
    return config
  },
  error => {
    console.error('Request error:', error)
    return Promise.reject(error)
  }
)

// 响应拦截器（容错重试机制）
service.interceptors.response.use(
  response => {
    const res = response.data
    
    // 如果返回的状态码不是success，则抛出错误
    if (!res.success && res.success !== undefined) {
      console.error('API Error:', res.error || res.message || 'Unknown error')
      return Promise.reject(new Error(res.error || res.message || 'Error'))
    }
    
    return res
  },
  async error => {
    console.error('Response error:', error)

    if (error.response?.status === 401 && !error.config?.__narraworldAuthRetry && typeof window !== 'undefined') {
      const token = window.prompt('请输入 NarraWorld 访问口令')
      if (token) {
        setAccessToken(token)
        error.config.__narraworldAuthRetry = true
        error.config.headers = {
          ...(error.config.headers || {}),
          Authorization: `Bearer ${token}`,
          'X-NarraWorld-Token': token
        }
        return service(error.config)
      }
    }
    
    // 处理超时
    if (error.code === 'ECONNABORTED' && error.message.includes('timeout')) {
      console.error('Request timeout')
    }
    
    // 处理网络错误
    if (error.message === 'Network Error') {
      console.error('Network error - please check your connection')
    }
    
    return Promise.reject(error)
  }
)

// 带重试的请求函数
export const requestWithRetry = async (requestFn, maxRetries = 3, delay = 1000) => {
  for (let i = 0; i < maxRetries; i++) {
    try {
      return await requestFn()
    } catch (error) {
      if (i === maxRetries - 1) throw error
      
      console.warn(`Request failed, retrying (${i + 1}/${maxRetries})...`)
      await new Promise(resolve => setTimeout(resolve, delay * Math.pow(2, i)))
    }
  }
}

export default service
