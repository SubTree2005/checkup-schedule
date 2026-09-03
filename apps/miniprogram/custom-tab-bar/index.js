Component({
  data: {
    hidden: false,
    selected: 0,
    ready: false,
    switching: false,
    list: [
      {
        pagePath: '/pages/index/index',
        text: '首页'
      },
      {
        pagePath: '/pages/record/record',
        text: '体检'
      },
      {
        pagePath: '/pages/mine/mine',
        text: '我的'
      }
    ]
  },

  lifetimes: {
    attached() {
      const pages = getCurrentPages()
      const route = pages.length ? `/${pages[pages.length - 1].route}` : ''
      const routeIndex = this.data.list.findIndex(item => item.pagePath === route)
      const app = getApp()
      const selected = routeIndex >= 0 ? routeIndex : Number(app.globalData.activeTabIndex || 0)
      app.globalData.activeTabIndex = selected
      this.setData({ selected, ready: true })
    }
  },

  methods: {
    select(index) {
      const selected = Number(index)
      if (!Number.isInteger(selected) || !this.data.list[selected]) return
      getApp().globalData.activeTabIndex = selected
      if (this.data.selected !== selected || !this.data.ready) this.setData({ selected, ready: true })
    },

    switchTab(e) {
      const index = Number(e.currentTarget.dataset.index)
      const item = this.data.list[index]
      if (!item || index === this.data.selected || this.data.switching) return
      getApp().globalData.activeTabIndex = index
      this.setData({ selected: index, switching: true })
      setTimeout(() => {
        wx.switchTab({
          url: item.pagePath,
          complete: () => this.setData({ switching: false })
        })
      }, 140)
    }
  }
})
