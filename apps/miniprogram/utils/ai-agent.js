const api = require('./api')

const SESSION_KEY = 'aiAgentSession'
const HISTORY_KEY = 'aiAgentHistory'
const LEGACY_CONFIG_KEY = 'aiAgentConfig'
const MODEL_CONFIG_KEY = 'aiAgentModelConfig'
const SESSION_TTL = 30 * 60 * 1000
const MAX_SESSION_MESSAGES = 20
const MAX_HISTORY_SESSIONS = 20
const DEFAULT_MODEL_LABEL = 'DeepSeek V4 Flash'

const INTRO = '你好，我是检畅 AI 助手。我可以帮你查找功能、打开体检页面、说明检查准备事项，也能解释体检报告中常见指标的一般含义。涉及诊断和治疗时，请以医生意见为准。'

function scopedStorageKey(baseKey) {
  const user = wx.getStorageSync('userInfo') || {}
  const userID = String(user.userID || user.id || 'anonymous')
  return `${baseKey}:${userID}`
}

function readStorage(baseKey) {
  const key = scopedStorageKey(baseKey)
  const scoped = wx.getStorageSync(key)
  if (scoped) return scoped
  const legacy = wx.getStorageSync(baseKey)
  if (!legacy) return legacy
  wx.setStorageSync(key, legacy)
  wx.removeStorageSync(baseKey)
  return legacy
}

function writeStorage(baseKey, value) {
  wx.setStorageSync(scopedStorageKey(baseKey), value)
}

function removeStorage(baseKey) {
  wx.removeStorageSync(scopedStorageKey(baseKey))
  wx.removeStorageSync(baseKey)
}

function trimMessages(messages) {
  const rows = Array.isArray(messages) ? messages : []
  if (rows.length <= MAX_SESSION_MESSAGES) return rows
  return [rows[0]].concat(rows.slice(-(MAX_SESSION_MESSAGES - 1)))
}

function uid() {
  return `chat-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`
}

function message(role, content, extras = {}) {
  return { id: uid(), role, content: String(content || ''), createdAt: Date.now(), ...extras }
}

function newSession() {
  const now = Date.now()
  return {
    id: uid(),
    title: '未命名会话',
    createdAt: now,
    updatedAt: now,
    expiresAt: now + SESSION_TTL,
    messages: [message('assistant', INTRO)]
  }
}

function history() {
  const rows = readStorage(HISTORY_KEY)
  if (!Array.isArray(rows)) return []
  const normalized = rows.slice(0, MAX_HISTORY_SESSIONS).map(session => ({
    ...session,
    messages: trimMessages(session.messages)
  }))
  if (rows.length > MAX_HISTORY_SESSIONS || rows.some(session => Array.isArray(session.messages) && session.messages.length > MAX_SESSION_MESSAGES)) {
    writeStorage(HISTORY_KEY, normalized)
  }
  return normalized
}

function archive(session) {
  if (!session || !Array.isArray(session.messages) || session.messages.length <= 1) return
  const rows = history().filter(item => item.id !== session.id)
  rows.unshift({ ...session, messages: trimMessages(session.messages), archivedAt: Date.now() })
  writeStorage(HISTORY_KEY, rows.slice(0, MAX_HISTORY_SESSIONS))
}

function ensureSession() {
  removeStorage(LEGACY_CONFIG_KEY)
  const cached = readStorage(SESSION_KEY)
  if (cached && Array.isArray(cached.messages) && Number(cached.expiresAt || 0) > Date.now()) {
    return cached.messages.length > MAX_SESSION_MESSAGES
      ? saveSession({ ...cached, messages: trimMessages(cached.messages) })
      : cached
  }
  if (cached) archive(cached)
  const session = newSession()
  writeStorage(SESSION_KEY, session)
  return session
}

function saveSession(session) {
  const now = Date.now()
  const next = { ...session, messages: trimMessages(session.messages), updatedAt: now, expiresAt: now + SESSION_TTL }
  writeStorage(SESSION_KEY, next)
  return next
}

function resumeSession(sessionID) {
  const selected = history().find(item => item.id === sessionID)
  if (!selected) return ensureSession()
  const current = readStorage(SESSION_KEY)
  if (current && current.id !== selected.id) archive(current)
  writeStorage(HISTORY_KEY, history().filter(item => item.id !== selected.id))
  return saveSession({ ...selected, archivedAt: undefined })
}

function makeTitle(value) {
  const text = String(value || '').replace(/\s+/g, ' ').trim()
  if (!text) return '未命名会话'
  return text.length > 12 ? `${text.slice(0, 12)}…` : text
}

function completeRound(session, userText, assistantText, assistantExtras = {}) {
  const messages = (session.messages || []).concat(
    message('user', userText),
    message('assistant', assistantText, assistantExtras)
  )
  return saveSession({
    ...session,
    title: session.title === '未命名会话' ? makeTitle(userText) : session.title,
    messages
  })
}

function setRecordTab(tab) {
  const pages = typeof getCurrentPages === 'function' ? getCurrentPages() : []
  const page = pages.length ? pages[pages.length - 1] : null
  if (page && page.route === 'pages/record/record' && typeof page.selectRecordTab === 'function') {
    wx.removeStorageSync('recordInitialTab')
    page.selectRecordTab(tab)
    return
  }
  wx.setStorageSync('recordInitialTab', tab)
  wx.switchTab({ url: '/pages/record/record' })
}

function currentPlan() {
  try {
    const app = getApp()
    return (app && app.globalData && app.globalData.currentPlan) || wx.getStorageSync('currentPlan') || null
  } catch (error) {
    return wx.getStorageSync('currentPlan') || null
  }
}

const ACTIONS = [
  {
    id: 'plan-overview',
    pattern: /(当前|本次)?体检(总览|进度)|查看.{0,3}(总览|进度)/,
    label: '查看体检总览',
    description: '查看本次体检各项目的完成情况与报告状态。',
    buttonText: '打开总览',
    run() {
      const plan = currentPlan()
      if (!plan || !plan.planID) return setRecordTab('appointments')
      wx.navigateTo({ url: `/pages/plan-overview/plan-overview?planID=${plan.planID}` })
    }
  },
  {
    id: 'current-exam',
    pattern: /(继续|开始|当前|实时).{0,4}体检|体检导航|去做体检|(打开|查看|前往|去).{0,4}(导航|路线)/,
    label: '进入当前体检',
    description: '打开当前项目、排队信息与院内导航入口。',
    buttonText: '进入体检',
    run() {
      const plan = currentPlan()
      if (!plan || !plan.planID) return setRecordTab('appointments')
      wx.navigateTo({ url: `/pages/plan/plan?planID=${plan.planID}` })
    }
  },
  {
    id: 'create-exam',
    pattern: /(创建|添加|新建|预约).{0,4}体检/,
    label: '添加体检',
    description: '选择医院、套餐或自选项目，创建新的体检安排。',
    buttonText: '去添加',
    run: () => wx.navigateTo({ url: '/pages/hospital/hospital' })
  },
  {
    id: 'exam-reports',
    pattern: /^(打开|查看|前往|去看|查找|帮我找)?(体检报告|报告记录|历史报告)$/,
    label: '查找体检报告',
    description: '前往历史体检，选择已出报告的记录后可查看详细结果。',
    buttonText: '查看记录',
    run: () => setRecordTab('history')
  },
  {
    id: 'exam-history',
    pattern: /(历史体检|体检历史|历史记录)/,
    label: '历史体检',
    description: '按年份查看已完成或已中断的体检记录。',
    buttonText: '查看历史',
    run: () => setRecordTab('history')
  },
  {
    id: 'appointments',
    pattern: /(预约体检|我的体检|体检预约|预约记录)/,
    label: '预约体检',
    description: '查看当前预约、进行中体检与后续安排。',
    buttonText: '查看预约',
    run: () => setRecordTab('appointments')
  },
  {
    id: 'profile',
    pattern: /(个人资料|个人信息|修改头像)/,
    label: '个人资料',
    description: '查看或修改头像与基础个人信息。',
    buttonText: '打开资料',
    run: () => wx.navigateTo({ url: '/pages/edit-profile/edit-profile' })
  },
  {
    id: 'account-security',
    pattern: /(账号与隐私|账户与隐私|账号设置|隐私设置|修改密码)/,
    label: '账号与隐私',
    description: '管理密码、协议和账号安全选项。',
    buttonText: '打开设置',
    run: () => wx.navigateTo({ url: '/pages/account-security/account-security' })
  },
  {
    id: 'agent-settings',
    pattern: /(AI|ai|助手).{0,4}(设置|接口|历史会话)/,
    label: 'AI 助手设置',
    description: '查看模型服务状态与历史会话。',
    buttonText: '打开设置',
    run: () => wx.navigateTo({ url: '/pages/ai-settings/ai-settings' })
  },
  {
    id: 'home',
    pattern: /(回到|返回|打开|前往|去).{0,3}(首页|主页)|^(首页|主页)$/,
    label: '首页',
    description: '返回首页查看当前最重要的体检安排与提醒。',
    buttonText: '返回首页',
    run: () => wx.switchTab({ url: '/pages/index/index' })
  }
]

function localAction(text) {
  const value = String(text || '').trim()
  return ACTIONS.find(item => item.pattern.test(value)) || null
}

function actionCard(action) {
  return {
    kind: 'navigation',
    actionID: action.id,
    title: action.label,
    description: action.description,
    buttonText: action.buttonText
  }
}

function runAction(actionID) {
  const action = ACTIONS.find(item => item.id === actionID)
  if (!action) return false
  action.run()
  return true
}

function getModelConfig() {
  const stored = readStorage(MODEL_CONFIG_KEY)
  if (!stored || stored.mode !== 'custom') {
    return { mode: 'default', model: '', apiKey: '' }
  }
  return {
    mode: 'custom',
    model: String(stored.model || '').trim(),
    apiKey: String(stored.apiKey || '').trim()
  }
}

function saveModelConfig(config = {}) {
  const model = String(config.model || '').trim()
  if (!model) throw new Error('请输入模型名称')
  const next = { mode: 'custom', model, apiKey: String(config.apiKey || '').trim() }
  writeStorage(MODEL_CONFIG_KEY, next)
  return next
}

function restoreDefaultModel() {
  removeStorage(MODEL_CONFIG_KEY)
  return { mode: 'default', model: '', apiKey: '' }
}

function startRequest(session, pageRoute) {
  let stopped = false
  const messages = (session.messages || [])
    .filter(item => (item.role === 'user' || item.role === 'assistant') && item.content)
    .slice(-20)
    .map(item => ({ role: item.role, content: item.content }))
  const modelConfig = getModelConfig()
  const requestData = { messages, currentPage: pageRoute || '' }
  if (modelConfig.mode === 'custom') {
    requestData.model = modelConfig.model
    if (modelConfig.apiKey) requestData.apiKey = modelConfig.apiKey
  }
  const promise = api.agent.chat(requestData).then(payload => {
    if (stopped) throw new Error('已停止生成')
    const reply = payload && payload.reply
    if (!reply) throw new Error('AI 服务未返回可显示的内容')
    return reply
  })
  return {
    promise,
    abort() { stopped = true }
  }
}

module.exports = {
  INTRO,
  SESSION_TTL,
  actionCard,
  completeRound,
  ensureSession,
  getHistory: history,
  getModelConfig,
  DEFAULT_MODEL_LABEL,
  localAction,
  makeMessage: message,
  resumeSession,
  restoreDefaultModel,
  runAction,
  saveModelConfig,
  saveSession,
  startRequest
}
