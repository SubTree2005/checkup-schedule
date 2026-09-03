Component({
  data: {
    hidden: false,
    selected: 0,
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

  methods: {
    switchTab(e) {
      const index = Number(e.currentTarget.dataset.index)
      const item = this.data.list[index]
      if (!item || index === this.data.selected) return
      wx.switchTab({ url: item.pagePath })
    }
  }
})
