const { navigationMetrics } = require('../../utils/layout')
const api = require('../../utils/api')
const flowGuard = require('../../utils/flow-guard')
const { backToRoute } = require('../../utils/navigation')
const app = getApp()
const MAX_MAP_POINTS = 100000

function confirmAction(title, content, confirmText) {
  return new Promise(resolve => {
    wx.showModal({
      title,
      content,
      confirmText,
      confirmColor: '#C43D4D',
      success: result => resolve(result.confirm),
      fail: () => resolve(false)
    })
  })
}

function validPoint(value) {
  return Array.isArray(value) && value.length >= 2 && Number.isFinite(value[0]) && Number.isFinite(value[1])
}

function collectPoints(value, output = []) {
  const pending = [value]
  while (pending.length) {
    const current = pending.pop()
    if (validPoint(current)) {
      if (output.length >= MAX_MAP_POINTS) return null
      output.push(current)
      continue
    }
    if (!Array.isArray(current)) continue
    for (let index = current.length - 1; index >= 0; index -= 1) pending.push(current[index])
  }
  return output
}

function polygonRings(geometry) {
  if (geometry.type === 'Polygon') return geometry.coordinates || []
  if (geometry.type === 'MultiPolygon') return (geometry.coordinates || []).reduce((rows, polygon) => rows.concat(polygon), [])
  return []
}

Page({
  data: {
    ...navigationMetrics(),
    planID: '',
    detailID: '',
    fromName: '当前检查点',
    toName: '下一检查科室',
    distance: '暂无路线数据',
    duration: '',
    location: '',
    floorInstruction: '请根据院内指引前往目标科室。',
    hasMap: false,
    canSkip: false,
    replanNotice: '',
    operating: false
  },
  onLoad(options) {
    if (!flowGuard.requireLogin(app)) return
    const currentPlan = app.globalData.currentPlan || {}
    const currentStep = (currentPlan.steps || []).find(step => ['active', 'pending'].includes(step.status)) || {}
    const planID = options.planID || currentPlan.planID || currentPlan.id
    const detailID = options.detailID || currentStep.detailID
    if (!planID || !detailID) return wx.redirectTo({ url: '/pages/plan/plan' })
    this._followingRoute = options.followRoute === '1' || (currentPlan.planID === planID && currentPlan.planStatus === '进行中')
    this._needsPlanCheck = true
    this.setData({ planID, detailID, canSkip: this._followingRoute && currentPlan.planStatus === '进行中' })
  },
  onShow() {
    if (!this.data.planID) return
    this._visible = true
    this.refreshNavigation()
  },
  onHide() { this._visible = false; clearTimeout(this._refreshTimer) },
  onUnload() { this.onHide() },
  async refreshNavigation() {
    clearTimeout(this._refreshTimer)
    const revision = this._actionRevision || 0
    try {
      if (this.data.operating) return
      if (this._followingRoute || this._needsPlanCheck) {
        const plan = await api.plans.get(this.data.planID)
        if (!this._visible || this.data.operating || this._leaving || revision !== (this._actionRevision || 0)) return
        this._needsPlanCheck = false
        if (plan.replanNotice) this._followingRoute = true
        if (!this._followingRoute && plan.planStatus === '进行中') {
          const current = (plan.steps || []).find(item => item.status === 'active') || (plan.steps || []).find(item => item.status === 'pending')
          const target = (plan.steps || []).find(item => item.detailID === this.data.detailID)
          this._followingRoute = !!current && (current.detailID === this.data.detailID || (target && target.status === 'skipped'))
        }
        if (this._followingRoute) {
          app.saveCurrentPlan(plan.finished ? null : plan)
          if (!this.applyRoutePlan(plan)) return
        }
      }
      const data = await api.plans.navigation(this.data.planID, this.data.detailID)
      if (this._visible && !this._leaving && !this.data.operating && revision === (this._actionRevision || 0)) this.applyNavigation(data)
    } catch (error) { if (!this._map) api.showError(error) }
    finally {
      if (this._visible && this._followingRoute && !this._leaving && !this.data.operating) {
        clearTimeout(this._refreshTimer)
        this._refreshTimer = setTimeout(() => this.refreshNavigation(), 15000)
      }
    }
  },
  applyRoutePlan(plan) {
    if (plan.finished) {
      app.globalData.viewingPlanRecord = plan
      this._leaving = true
      clearTimeout(this._refreshTimer)
      wx.redirectTo({ url: `/pages/plan-complete/plan-complete?id=${plan.planID}&ended=${plan.ended ? '1' : '0'}` })
      return false
    }
    const step = (plan.steps || []).find(item => item.status === 'active') || (plan.steps || []).find(item => item.status === 'pending')
    if (!step) { this.setData({ canSkip: false }); return false }
    if (step.detailID !== this.data.detailID) {
      this._map = null
      this.setData({ hasMap: false, toName: step.department || step.title, location: '正在更新下一检查点的路线', distance: '暂无路线数据', duration: '', floorInstruction: '正在更新导航' })
    }
    this.setData({ detailID: step.detailID, canSkip: plan.planStatus === '进行中', replanNotice: plan.replanNotice || this.data.replanNotice })
    return true
  },
  async skipCurrent() {
    if (!this.data.canSkip || this.data.operating) return
    const detailID = this.data.detailID
    const confirmed = await confirmAction('跳过此项', '此项将保留为未完成，并重新导航到下一个检查点。结束体检后可预约未完成项目。', '确认跳过')
    if (!confirmed) return
    await this.runAction(async () => {
      const plan = await api.plans.skip(this.data.planID, detailID)
      app.saveCurrentPlan(plan.finished ? null : plan)
      app.globalData.viewingPlanRecord = plan
      if (!this._visible) return plan
      if (this.applyRoutePlan(plan)) {
        try {
          const navigation = await api.plans.navigation(this.data.planID, this.data.detailID)
          if (this._visible && !this._leaving) this.applyNavigation(navigation)
        } catch (error) {
          // Skipping already succeeded; retain the new destination and let polling
          // retry the map request without repeating the skip mutation.
          this.setData({ location: '路线暂时加载失败，正在重试，请以院内指引为准。' })
          api.showError(error)
        }
      }
      return plan
    })
  },
  applyNavigation(data) {
    this._map = data.map || null
    this.setData({
      fromName: data.fromName,
      toName: data.toName,
      distance: data.distanceMeters === null ? '暂无路线数据' : `${data.distanceMeters} 米`,
      duration: data.durationMinutes === null ? '' : `约 ${data.durationMinutes} 分钟`,
      location: data.location || '',
      floorInstruction: data.floorInstruction || '请根据院内指引前往目标科室。',
      hasMap: !!data.map
    }, () => {
      if (data.map) wx.nextTick(() => this.drawIndoorMap())
    })
  },
  drawIndoorMap() {
    const map = this._map
    if (!map || !map.geojson) return
    this.createSelectorQuery().select('#indoorMap').boundingClientRect(rect => {
      if (!rect || !rect.width || !rect.height) return
      const features = map.geojson.features || []
      const allPoints = []
      const mapWithinLimit = features.every(feature => collectPoints((feature.geometry || {}).coordinates, allPoints))
      const routeWithinLimit = mapWithinLimit && collectPoints(map.routeCoordinates || [], allPoints)
      if (!routeWithinLimit) {
        this.setData({
          hasMap: false,
          location: '地图数据过大，暂无法绘制，请以现场标识为准。'
        })
        return
      }
      if (!allPoints.length) return
      let minX = Infinity
      let maxX = -Infinity
      let minY = Infinity
      let maxY = -Infinity
      allPoints.forEach(point => {
        minX = Math.min(minX, point[0])
        maxX = Math.max(maxX, point[0])
        minY = Math.min(minY, point[1])
        maxY = Math.max(maxY, point[1])
      })
      const padding = 18
      const xRange = Math.max(maxX - minX, 0.000001)
      const yRange = Math.max(maxY - minY, 0.000001)
      const scale = Math.min((rect.width - padding * 2) / xRange, (rect.height - padding * 2) / yRange)
      const xOffset = (rect.width - xRange * scale) / 2
      const yOffset = (rect.height - yRange * scale) / 2
      const project = point => [xOffset + (point[0] - minX) * scale, rect.height - yOffset - (point[1] - minY) * scale]
      const context = wx.createCanvasContext('indoorMap', this)
      context.setFillStyle('#F8FAFC')
      context.fillRect(0, 0, rect.width, rect.height)

      const drawRing = (ring, fill, stroke, width) => {
        const points = (ring || []).filter(validPoint).map(project)
        if (points.length < 2) return
        context.beginPath()
        context.moveTo(points[0][0], points[0][1])
        points.slice(1).forEach(point => context.lineTo(point[0], point[1]))
        context.closePath()
        context.setFillStyle(fill)
        context.fill()
        context.setStrokeStyle(stroke)
        context.setLineWidth(width)
        context.stroke()
      }
      const polygonFeatures = features.filter(feature => ['Polygon', 'MultiPolygon'].includes((feature.geometry || {}).type))
      polygonFeatures.forEach(feature => {
        const type = (feature.properties || {}).featureType
        const fill = type === 'buildingOutline' ? '#EEF4FA' : '#FFFFFF'
        const stroke = type === 'buildingOutline' ? '#94A3B8' : '#CBD5E1'
        polygonRings(feature.geometry || {}).forEach(ring => drawRing(ring, fill, stroke, type === 'buildingOutline' ? 1.5 : 0.7))
      })

      features.filter(feature => ['corridor', 'route'].includes((feature.properties || {}).featureType)).forEach(feature => {
        const points = ((feature.geometry || {}).coordinates || []).filter(validPoint).map(project)
        if (points.length < 2) return
        context.beginPath()
        context.moveTo(points[0][0], points[0][1])
        points.slice(1).forEach(point => context.lineTo(point[0], point[1]))
        context.setStrokeStyle('#94A3B8')
        context.setLineWidth(2)
        context.stroke()
      })

      const routePoints = (map.routeCoordinates || []).filter(validPoint).map(project)
      if (routePoints.length > 1) {
        context.beginPath()
        context.moveTo(routePoints[0][0], routePoints[0][1])
        routePoints.slice(1).forEach(point => context.lineTo(point[0], point[1]))
        context.setStrokeStyle('#1350BE')
        context.setLineWidth(5)
        context.setLineCap('round')
        context.setLineJoin('round')
        context.stroke()
      }

      const drawMarker = (point, color, label) => {
        if (!point || !validPoint(point.coordinates)) return
        const projected = project(point.coordinates)
        context.beginPath()
        context.arc(projected[0], projected[1], 7, 0, Math.PI * 2)
        context.setFillStyle(color)
        context.fill()
        context.setStrokeStyle('#FFFFFF')
        context.setLineWidth(2)
        context.stroke()
        context.setFillStyle('#0F172A')
        context.setFontSize(11)
        const measured = typeof context.measureText === 'function' ? context.measureText(label) : null
        const labelWidth = measured && measured.width ? measured.width : label.length * 11
        const preferredX = projected[0] + 10
        const labelX = preferredX + labelWidth > rect.width - 8
          ? Math.max(8, projected[0] - labelWidth - 10)
          : preferredX
        context.fillText(label, labelX, Math.max(14, projected[1] - 8))
      }
      drawMarker(map.fromPoint, '#F59E0B', map.fromPoint ? `起：${map.fromPoint.name}` : '')
      drawMarker(map.toPoint, '#16A34A', `终：${map.toPoint.name}`)
      context.draw()
    }).exec()
  },
  completeNavigation() {
    backToRoute('pages/plan/plan', `/pages/plan/plan?planID=${this.data.planID}`)
  },
  goOverview() { wx.navigateTo({ url: `/pages/plan-overview/plan-overview?planID=${this.data.planID}` }) },
  async runAction(action) {
    if (this.data.operating) return null
    this.setData({ operating: true })
    this._actionRevision = (this._actionRevision || 0) + 1
    clearTimeout(this._refreshTimer)
    try {
      const updated = await action()
      app.saveCurrentPlan(updated.finished ? null : updated)
      app.globalData.viewingPlanRecord = updated
      return updated
    } catch (error) {
      api.showError(error)
      return null
    } finally {
      this.setData({ operating: false })
      if (this._visible && this._followingRoute && !this._leaving) this._refreshTimer = setTimeout(() => this.refreshNavigation(), 15000)
    }
  },
  async pausePlan() {
    const confirmed = await confirmAction('中断体检', '将保留当前进度，之后继续时会重新安排后续路线。', '确认中断')
    if (!confirmed) return
    const updated = await this.runAction(() => api.plans.pause(this.data.planID))
    if (updated) { this._leaving = true; clearTimeout(this._refreshTimer); wx.switchTab({ url: '/pages/index/index' }) }
  },
  async finishPlan() {
    const confirmed = await confirmAction('结束体检', '将保留当前记录，未完成项目可在结束后另行预约。', '确认结束')
    if (!confirmed) return
    const updated = await this.runAction(() => api.plans.finish(this.data.planID))
    if (updated) { this._leaving = true; clearTimeout(this._refreshTimer); wx.redirectTo({ url: `/pages/plan-complete/plan-complete?id=${updated.planID}&ended=1` }) }
  },
  goBack() { wx.navigateBack({ delta: 1 }) }
})
