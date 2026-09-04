const agent = require('../../utils/ai-agent')

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
    statusBarHeight: 44
  },

  lifetimes: {
    attached() {
      const info = wx.getWindowInfo ? wx.getWindowInfo() : wx.getSystemInfoSync()
      this.setData({ statusBarHeight: Number(info.statusBarHeight || 44) })
    },
    detached() {
      if (this._request) this._request.abort()
      if (this._localResponseTimer) clearTimeout(this._localResponseTimer)
      this.setTabBarHidden(false)
    }
  },

  methods: {
    noop() {},

    setTabBarHidden(hidden) {
      if (!this.data.withTabBar) return
      const pages = getCurrentPages()
      const page = pages.length ? pages[pages.length - 1] : null
      const tabBar = page && typeof page.getTabBar === 'function' ? page.getTabBar() : null
      if (tabBar) tabBar.setData({ hidden })
      if (hidden && wx.hideTabBar) wx.hideTabBar({ animation: false })
      if (!hidden && wx.showTabBar) wx.showTabBar({ animation: false })
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

    openSettings() {
      if (this.data.thinking) return
      this.setTabBarHidden(false)
      this.setData({ opened: false, inputFocused: false, keyboardHeight: 0 })
      wx.hideKeyboard()
      const pages = getCurrentPages()
      const route = pages.length ? pages[pages.length - 1].route : ''
      if (route !== 'pages/ai-settings/ai-settings') wx.navigateTo({ url: '/pages/ai-settings/ai-settings' })
    },

    triggerAdd() { this.triggerEvent('add') },

    onInput(event) { this.setData({ draft: event.detail.value }) },
    onFocus() { this.setData({ inputFocused: true }) },
    onBlur() { this.setData({ inputFocused: false }) },
    onKeyboardHeight(event) { this.setData({ keyboardHeight: Number(event.detail.height || 0) }) },

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
      this._request = agent.startRequest(requestSession, pageRoute)
      try {
        const reply = await this._request.promise
        if (this.data.thinking) this.finishResponse(text, reply)
      } catch (error) {
        if (this.data.thinking) this.finishResponse(text, `暂时无法完成回答：${error.message || 'AI 服务连接失败'}`)
      } finally {
        this._request = null
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
