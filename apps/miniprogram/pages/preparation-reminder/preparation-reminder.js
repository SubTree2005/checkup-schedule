const app = getApp()
const api = require('../../utils/api')
const planFlow = require('../../utils/plan-flow')
const flowGuard = require('../../utils/flow-guard')

Page({
  data: {
    requirements: [],
    wechatPush: false,
    wechatPushAvailable: false,
    subscriptionTemplateIds: [],
    reminderStatusText: '正在检查消息推送服务',
    systemCalendar: true,
    submitting: false
  },

  async onLoad() {
    if (!flowGuard.requireAppointment(app)) return
    this.applyRequirements(planFlow.preparationRequirements(app, false))
    try {
      const config = await api.reminders.config()
      const ids = config.templateIDs || []
      const available = config.available === true && ids.length > 0
      this.setData({
        wechatPushAvailable: available,
        wechatPush: available,
        subscriptionTemplateIds: ids,
        reminderStatusText: available ? (config.schedule || '按预约时间发送提醒') : '消息推送服务暂不可用'
      })
    } catch (error) {
      this.setData({
        wechatPushAvailable: false,
        wechatPush: false,
        reminderStatusText: '消息推送服务暂不可用'
      })
    }
  },

  applyRequirements(requirements) { this.setData({ requirements }) },

  setReminder(e) {
    const key = e.currentTarget.dataset.key
    if (key === 'wechatPush' && !this.data.wechatPushAvailable) return
    this.setData({ [key]: e.detail.value })
  },

  async confirmAppointment() {
    if (this.data.submitting) return
    this.setData({ submitting: true })
    try {
      const splitDraft = app.globalData.splitPlanDraft
      let reminderSubscription = null
      if (this.data.wechatPush) {
        const result = await planFlow.requestWeChatPush(this.data.subscriptionTemplateIds)
        const acceptedTemplateID = this.data.subscriptionTemplateIds.find(id => result[id] === 'accept')
        if (acceptedTemplateID) {
          reminderSubscription = { templateID: acceptedTemplateID, permission: 'accept' }
        }
      }
      let activePlan = splitDraft && splitDraft.activePlan
      if (splitDraft && !activePlan) {
        activePlan = await planFlow.createPlanForItems(app, splitDraft.readyItemIDs, {
          planMode: 'realtime',
          booked: 'no',
          preparationDecision: 'split-current'
        }, {
          packageID: splitDraft.packageID,
          includeAppointmentDraft: false
        })
        splitDraft.activePlan = activePlan
      }
      const plan = await planFlow.createPlan(app, {
        planMode: 'appointment',
        booked: 'yes',
        preparationDecision: app.globalData.preparationDecision || 'scheduled',
        wechatPushEnabled: !!reminderSubscription,
        reminderSubscription,
        systemCalendarEnabled: this.data.systemCalendar
      })
      if (this.data.systemCalendar) await planFlow.addSystemCalendar(app)
      if (activePlan) app.saveCurrentPlan(activePlan)
      else app.saveCurrentPlan(plan)
      app.globalData.splitPlanDraft = null
      wx.showToast({
        title: this.data.wechatPush && !reminderSubscription ? '预约成功，未开启微信提醒' : '预约已创建',
        icon: reminderSubscription ? 'success' : 'none'
      })
      setTimeout(() => wx.switchTab({ url: '/pages/record/record' }), 350)
    } catch (error) {
      api.showError(error)
    } finally {
      this.setData({ submitting: false })
    }
  },

  goBack() { wx.navigateBack({ delta: 1 }) }
})
