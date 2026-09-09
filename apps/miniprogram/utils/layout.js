function navigationMetrics() {
  const windowInfo = wx.getWindowInfo ? wx.getWindowInfo() : wx.getSystemInfoSync ? wx.getSystemInfoSync() : {}
  const statusBarHeight = Number.isFinite(windowInfo.statusBarHeight) ? windowInfo.statusBarHeight : 24
  let menuButton = null
  try {
    menuButton = wx.getMenuButtonBoundingClientRect ? wx.getMenuButtonBoundingClientRect() : null
  } catch (_error) {
    menuButton = null
  }

  const menuTop = Number(menuButton && menuButton.top) || statusBarHeight + 6
  const menuHeight = Number(menuButton && menuButton.height) || 32
  const navigationBarHeight = menuHeight + Math.max(0, menuTop - statusBarHeight) * 2
  const windowWidth = Number(windowInfo.windowWidth) || 375
  const menuWidth = Number(menuButton && menuButton.width) || 87
  const menuLeft = Number(menuButton && menuButton.left) || windowWidth - menuWidth - 7
  const navigationSideWidth = windowWidth - menuLeft + 8
  const navigationStyle = `--status-bar-height:${statusBarHeight}px;--navigation-bar-height:${navigationBarHeight}px;--navigation-side-width:${navigationSideWidth}px;`
  return { statusBarHeight, navigationBarHeight, navigationSideWidth, navigationStyle }
}

module.exports = { navigationMetrics }
