const { navigationMetrics } = require('../../utils/layout')
const app = getApp()
const api = require('../../utils/api')
const planFlow = require('../../utils/plan-flow')
const flowGuard = require('../../utils/flow-guard')

Page({
  data: {
    ...navigationMetrics(),
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
      await planFlow.refreshRestrictions(app)
      if (planFlow.needsAppointmentFastingConfirmation(app)) {
        const result = await new Promise((resolve, reject) => wx.showModal({
          title: '确认空腹准备',
          content: '距离预约不足 8 小时，所选项目需要空腹。您是否已经连续空腹至少 8 小时？',
          confirmText: '已空腹8小时',
          cancelText: '尚未满足',
          success: resolve,
          fail: reject
        }))
        await planFlow.savePreparation(app, { fasting: result.confirm ? 'yes' : 'no' })
        if (!result.confirm) {
          wx.showToast({ title: '请返回上一步，选择留有充足准备时间的预约时段', icon: 'none' })
          return
        }
      }
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
      app.globalData.followUpPlanDraft = null
      wx.showToast({
        title: this.data.wechatPush && !reminderSubscription ? '预约成功，未开启微信提醒' : '预约已创建',
        icon: reminderSubscription ? 'success' : 'none'
      })
      wx.switchTab({ url: '/pages/record/record' })
    } catch (error) {
      api.showError(error)
    } finally {
      this.setData({ submitting: false })
    }
  },

  goBack() { wx.navigateBack({ delta: 1 }) }
})
