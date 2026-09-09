const agent = require('../../utils/ai-agent')
const { navigationMetrics } = require('../../utils/layout')

Component({
  properties: {
    withTabBar: { type: Boolean, value: false },
    showAdd: { type: Boolean, value: false }
  },

  data: {
    opened: false,
    title: '未命名会话',
    messages: [],
    draft: '',
    inputFocused: false,
    keyboardHeight: 0,
    thinking: false,
    scrollIntoView: '',
    headerTop: 88,
    viewportHeight: 0
  },

  lifetimes: {
    attached() {
      this.updateViewport()
    },
    detached() {
      if (this._request) this._request.abort()
      if (this._localResponseTimer) clearTimeout(this._localResponseTimer)
      this.setTabBarHidden(false)
    }
  },

  methods: {
    noop() {},

    updateViewport() {
      const info = wx.getWindowInfo ? wx.getWindowInfo() : wx.getSystemInfoSync()
      const navigation = navigationMetrics()
      const headerTop = navigation.statusBarHeight
      const menu = wx.getMenuButtonBoundingClientRect ? wx.getMenuButtonBoundingClientRect() : null
      const headerRight = menu && menu.left ? Math.max(96, Number(info.windowWidth || 375) - menu.left + 8) : 104
      this.setData({ headerTop, headerRight, headerHeight: navigation.navigationBarHeight, viewportHeight: Number(info.windowHeight) || 667, keyboardHeight: 0 })
    },

    setTabBarHidden(hidden) {
      if (!this.data.withTabBar) return
      const pages = getCurrentPages()
      const page = pages.length ? pages[pages.length - 1] : null
      const tabBar = page && typeof page.getTabBar === 'function' ? page.getTabBar() : null
      if (tabBar) tabBar.setData({ hidden })
    },

    openChat() {
      const session = agent.ensureSession()
      this.showSession(session)
    },

    openSessionById(sessionID) {
      const session = agent.resumeSession(sessionID)
      this.showSession(session)
    },

    showSession(session) {
      this._session = session
      this.updateViewport()
      this.setTabBarHidden(true)
      this.setData({
        opened: true,
        title: session.title,
        messages: session.messages,
        scrollIntoView: session.messages.length ? `message-${session.messages[session.messages.length - 1].id}` : ''
      })
    },

    closeChat() {
      if (this.data.thinking) return
      this.setTabBarHidden(false)
      this.setData({ opened: false, inputFocused: false, keyboardHeight: 0 })
      wx.hideKeyboard()
    },

    startNewConversation() {
      if (this.data.thinking) this.stopThinking()
      this.setData({ draft: '', inputFocused: false, keyboardHeight: 0, thinking: false })
      wx.hideKeyboard()
      this.showSession(agent.startNewSession())
    },

    triggerAdd() { this.triggerEvent('add') },

    onInput(event) { this.setData({ draft: event.detail.value }) },
    onFocus() { this.setData({ inputFocused: true }) },
    onBlur() { this.setData({ inputFocused: false, keyboardHeight: 0 }) },
    onKeyboardHeight(event) {
      if (!this.data.opened) return
      const height = Number(event.detail.height)
      const maxHeight = Math.max(0, this.data.viewportHeight - this.data.headerTop - 100)
      this.setData({ keyboardHeight: Number.isFinite(height) ? Math.min(maxHeight, Math.max(0, height)) : 0 })
    },

    handleOrbTap() {
      if (this.data.thinking) return this.stopThinking()
      this.sendMessage()
    },

    async sendMessage() {
      if (this.data.thinking) return
      const text = String(this.data.draft || '').trim()
      if (!text) {
        this.setData({ inputFocused: true })
        return
      }
      wx.hideKeyboard()
      const userMessage = agent.makeMessage('user', text)
      const pendingMessages = (this._session.messages || []).concat(userMessage)
      this.setData({
        draft: '',
        inputFocused: false,
        keyboardHeight: 0,
        thinking: true,
        messages: pendingMessages,
        scrollIntoView: `message-${userMessage.id}`
      })

      const action = agent.localAction(text)
      if (action) {
        const reply = `可以，从下面的卡片进入“${action.label}”。`
        this._localResponseTimer = setTimeout(() => {
          this._localResponseTimer = null
          if (!this.data.thinking) return
          this.finishResponse(text, reply, agent.actionCard(action))
        }, 320)
        return
      }

      const pages = getCurrentPages()
      const pageRoute = pages.length ? pages[pages.length - 1].route : ''
      const requestSession = { ...this._session, messages: pendingMessages }
      const request = agent.startRequest(requestSession, pageRoute)
      this._request = request
      try {
        const reply = await request.promise
        if (this._request === request && this.data.thinking) this.finishResponse(text, reply)
      } catch (error) {
        if (this._request === request && this.data.thinking) this.finishResponse(text, `暂时无法完成回答：${error.message || 'AI 服务连接失败'}`)
      } finally {
        if (this._request === request) this._request = null
      }
    },

    finishResponse(userText, reply, card = null) {
      this._session = agent.completeRound(this._session, userText, reply, card ? { card } : {})
      this.setData({
        thinking: false,
        title: this._session.title,
        messages: this._session.messages,
        scrollIntoView: `message-${this._session.messages[this._session.messages.length - 1].id}`
      })
    },

    handleMessageAction(event) {
      if (this.data.thinking) return
      const actionID = event.currentTarget.dataset.actionId
      this.setTabBarHidden(false)
      this.setData({ opened: false, inputFocused: false, keyboardHeight: 0 }, () => {
        wx.hideKeyboard()
        if (!agent.runAction(actionID)) wx.showToast({ title: '该操作暂不可用', icon: 'none' })
      })
    },

    stopThinking() {
      if (this._request) this._request.abort()
      this._request = null
      if (this._localResponseTimer) clearTimeout(this._localResponseTimer)
      this._localResponseTimer = null
      const stopMessage = agent.makeMessage('assistant', '已停止生成。')
      this._session = agent.saveSession({ ...this._session, messages: (this.data.messages || []).concat(stopMessage) })
      this.setData({ thinking: false, messages: this._session.messages, scrollIntoView: `message-${stopMessage.id}` })
    }
  }
})
