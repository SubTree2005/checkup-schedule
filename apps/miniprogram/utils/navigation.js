function backToRoute(route, fallbackUrl) {
  const pages = typeof getCurrentPages === 'function' ? getCurrentPages() : []
  for (let index = pages.length - 2; index >= 0; index -= 1) {
    if (pages[index].route !== route) continue
    const delta = pages.length - 1 - index
    wx.navigateBack({
      delta,
      fail: () => wx.redirectTo({ url: fallbackUrl })
    })
    return
  }
  wx.redirectTo({ url: fallbackUrl })
}

module.exports = { backToRoute }
