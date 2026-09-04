function navigationMetrics() {
  const windowInfo = wx.getWindowInfo ? wx.getWindowInfo() : wx.getSystemInfoSync()
  const statusBarHeight = Number(windowInfo.statusBarHeight || 24)
  let menuButton = null
  try {
    menuButton = wx.getMenuButtonBoundingClientRect ? wx.getMenuButtonBoundingClientRect() : null
  } catch (_error) {
    menuButton = null
  }

  const menuTop = Number(menuButton && menuButton.top) || statusBarHeight + 6
  const menuHeight = Number(menuButton && menuButton.height) || 32
  const navigationBarHeight = Math.max(44, menuHeight + Math.max(0, menuTop - statusBarHeight) * 2)
  return { statusBarHeight, navigationBarHeight }
}

module.exports = { navigationMetrics }
