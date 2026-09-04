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
    planID: '',
    detailID: '',
    fromName: '当前检查点',
    toName: '下一检查科室',
    distance: '暂无路线数据',
    duration: '',
    location: '',
    floorInstruction: '请根据院内指引前往目标科室。',
    hasMap: false,
    operating: false
  },
  onLoad(options) {
    if (!flowGuard.requireLogin(app)) return
    const currentPlan = app.globalData.currentPlan || {}
    const currentStep = (currentPlan.steps || []).find(step => ['active', 'pending'].includes(step.status)) || {}
    const planID = options.planID || currentPlan.planID || currentPlan.id
    const detailID = options.detailID || currentStep.detailID
    if (!planID || !detailID) return wx.redirectTo({ url: '/pages/plan/plan' })
    this.setData({ planID, detailID })
    api.plans.navigation(planID, detailID).then(data => this.applyNavigation(data)).catch(api.showError)
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
    try {
      const updated = await action()
      app.saveCurrentPlan(updated.finished ? null : updated)
      return updated
    } catch (error) {
      api.showError(error)
      return null
    } finally {
      this.setData({ operating: false })
    }
  },
  async pausePlan() {
    const confirmed = await confirmAction('中断体检', '将保留当前进度，之后继续时会重新安排后续路线。', '确认中断')
    if (!confirmed) return
    const updated = await this.runAction(() => api.plans.pause(this.data.planID))
    if (updated) wx.switchTab({ url: '/pages/index/index' })
  },
  async finishPlan() {
    const confirmed = await confirmAction('结束体检', '未完成的项目将保留为未完成，本次体检结束后不可继续。', '确认结束')
    if (!confirmed) return
    const updated = await this.runAction(() => api.plans.finish(this.data.planID))
    if (updated) wx.redirectTo({ url: `/pages/plan-complete/plan-complete?id=${updated.planID}&ended=1` })
  },
  goBack() { wx.navigateBack({ delta: 1 }) }
})
