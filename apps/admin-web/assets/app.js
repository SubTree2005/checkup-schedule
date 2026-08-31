(function () {
  "use strict";

  const state = { me: null, departments: [], exams: [], packages: [], gis: [], dashboard: null, anomalies: [] };
  const byId = (id) => document.getElementById(id);
  const authView = byId("authView");
  const appView = byId("appView");
  const dialog = byId("editorDialog");
  const dialogBody = byId("dialogBody");
  let toastTimer;
  let workspaceImportPayload = null;

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function toast(message, type) {
    const node = byId("toast");
    node.textContent = message;
    node.className = "toast show" + (type === "error" ? " error" : "");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { node.className = "toast"; }, 3200);
  }

  async function api(path, options) {
    const config = Object.assign({ credentials: "same-origin" }, options || {});
    if (config.body && typeof config.body !== "string") {
      config.headers = Object.assign({ "Content-Type": "application/json" }, config.headers || {});
      config.body = JSON.stringify(config.body);
    }
    const response = await fetch("/api" + path, config);
    if (response.status === 401) {
      showAuth();
      throw new Error("登录已过期，请重新登录");
    }
    if (!response.ok) {
      let message = "请求失败";
      try {
        const payload = await response.json();
        if (typeof payload.detail === "string") message = payload.detail;
        if (Array.isArray(payload.detail)) message = payload.detail.map((item) => item.msg).join("；");
      } catch (_) {}
      throw new Error(message);
    }
    return response.status === 204 ? null : response.json();
  }

  function showAuth() {
    state.me = null;
    authView.classList.remove("hidden");
    appView.classList.add("hidden");
  }

  function showApp() {
    authView.classList.add("hidden");
    appView.classList.remove("hidden");
  }

  function formObject(form) {
    return Object.fromEntries(new FormData(form).entries());
  }

  document.querySelectorAll("[data-auth-tab]").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelectorAll("[data-auth-tab]").forEach((item) => item.classList.toggle("active", item === button));
      byId("loginForm").classList.toggle("hidden", button.dataset.authTab !== "login");
      byId("registerForm").classList.toggle("hidden", button.dataset.authTab !== "register");
    });
  });

  byId("loginForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      state.me = await api("/auth/login", { method: "POST", body: formObject(event.currentTarget) });
      showApp();
      await loadWorkspace();
      toast("登录成功");
    } catch (error) { toast(error.message, "error"); }
  });

  byId("registerForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const payload = formObject(event.currentTarget);
    payload.openTime = "08:00-17:00";
    try {
      state.me = await api("/auth/register", { method: "POST", body: payload });
      showApp();
      await loadWorkspace();
      toast("医院账号创建成功");
    } catch (error) { toast(error.message, "error"); }
  });

  byId("logoutButton").addEventListener("click", async () => {
    try { await api("/auth/logout", { method: "POST" }); } catch (_) {}
    showAuth();
  });

  document.querySelectorAll(".nav-item").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelectorAll(".nav-item").forEach((item) => item.classList.toggle("active", item === button));
      document.querySelectorAll(".page").forEach((page) => page.classList.remove("active"));
      byId("page-" + button.dataset.page).classList.add("active");
      document.querySelector(".sidebar").classList.remove("open");
    });
  });

  byId("menuButton").addEventListener("click", () => document.querySelector(".sidebar").classList.toggle("open"));
  byId("refreshButton").addEventListener("click", async () => {
    try { await loadWorkspace(); toast("数据已刷新"); } catch (error) { toast(error.message, "error"); }
  });

  async function loadWorkspace() {
    if (!state.me) state.me = await api("/auth/me");
    byId("hospitalName").textContent = state.me.hospital.hospitalName;
    byId("adminName").textContent = state.me.user.name;
    byId("adminPhone").textContent = state.me.user.phone;
    byId("avatar").textContent = state.me.user.name.slice(0, 1);
    const results = await Promise.all([
      api("/departments"),
      api("/exams"),
      api("/packages"),
      api("/gis"),
      api("/dashboard/summary"),
      api("/anomalies")
    ]);
    state.departments = results[0];
    state.exams = results[1];
    state.packages = results[2];
    state.gis = results[3];
    state.dashboard = results[4];
    state.anomalies = results[5];
    renderEverything();
  }

  function renderEverything() {
    renderDashboard();
    renderDepartments();
    renderExams();
    renderPackages();
    renderGisVersions();
    renderAdjustmentOptions();
    renderAnomalies();
  }

  function metricCard(label, value, note, alert) {
    return '<article class="metric-card' + (alert ? ' alert' : '') + '"><span class="metric-label">' +
      escapeHtml(label) + '</span><strong>' + escapeHtml(value) + '</strong><small>' + escapeHtml(note) + '</small></article>';
  }

  function renderDashboard() {
    const metrics = state.dashboard.metrics;
    byId("metricGrid").innerHTML =
      metricCard("今日规划", metrics.todayPlans, metrics.inProgressPlans + " 人正在体检") +
      metricCard("今日已完成", metrics.completedPlans, metrics.todayServed + " 项服务记录") +
      metricCard("平均等待", Math.round(metrics.averageWaitSeconds / 60) + " 分钟", "来自今日科室反馈") +
      metricCard("现场异常", metrics.unresolvedAnomalies, metrics.openDepartments + "/" + metrics.departmentCount + " 科室开放", metrics.unresolvedAnomalies > 0);
    byId("generatedAt").textContent = "更新于 " + formatTime(state.dashboard.generatedAt);
    renderFlowList(state.dashboard.flow);
    const floorSelect = byId("dashboardFloor");
    const previous = floorSelect.value;
    floorSelect.innerHTML = state.gis.length
      ? state.gis.map((item) => '<option value="' + escapeHtml(item.floorKey) + '">' + escapeHtml(item.floorKey) + '</option>').join("")
      : '<option value="">尚无地图</option>';
    if (state.gis.some((item) => item.floorKey === previous)) floorSelect.value = previous;
    if (floorSelect.value) loadDashboardMap(floorSelect.value);
    else renderMapEmpty(byId("dashboardMap"), "尚未上传院内 GIS", "前往“院内 GIS”上传 GeoJSON 后，人流会自动显示在地图上。");
  }

  function renderFlowList(flow) {
    const sorted = flow.slice().sort((a, b) => b.peopleFlow - a.peopleFlow);
    const max = Math.max(1, ...sorted.map((item) => item.peopleFlow));
    byId("flowList").innerHTML = sorted.length ? sorted.map((item) => {
      const wait = Math.round(item.estimatedWaitTime / 60);
      return '<div class="flow-row"><header><b>' + escapeHtml(item.deptName) + '</b><b>' + item.peopleFlow +
        ' 人</b></header><div class="progress"><i style="width:' + Math.max(4, item.peopleFlow / max * 100) +
        '%"></i></div><div class="flow-meta"><span>' + escapeHtml(item.location || "未设置位置") +
        '</span><span>预计等待 ' + wait + ' 分钟</span></div></div>';
    }).join("") : emptyState("暂无人流数据", "更新排队快照后将在这里显示");
  }

  byId("dashboardFloor").addEventListener("change", (event) => {
    if (event.target.value) loadDashboardMap(event.target.value);
  });

  async function loadDashboardMap(floor) {
    try {
      const data = await api("/dashboard/map/" + encodeURIComponent(floor));
      renderMap(byId("dashboardMap"), data.geojson, data.flow);
    } catch (error) {
      renderMapEmpty(byId("dashboardMap"), "地图载入失败", error.message);
    }
  }

  function emptyState(title, copy) {
    return '<div class="empty-state"><b>' + escapeHtml(title) + '</b><span>' + escapeHtml(copy) + '</span></div>';
  }

  function renderMapEmpty(container, title, copy) {
    container.innerHTML = '<div class="map-empty"><b>' + escapeHtml(title) + '</b>' + escapeHtml(copy) + '</div>';
  }

  function allCoordinates(value, result) {
    if (!Array.isArray(value)) return;
    if (value.length >= 2 && typeof value[0] === "number" && typeof value[1] === "number") {
      result.push([value[0], value[1]]);
    } else {
      value.forEach((item) => allCoordinates(item, result));
    }
  }

  function renderMap(container, geojson, flow) {
    const coordinates = [];
    (geojson.features || []).forEach((feature) => allCoordinates(feature.geometry && feature.geometry.coordinates, coordinates));
    if (!coordinates.length) {
      renderMapEmpty(container, "地图没有可显示坐标", "请检查 GeoJSON geometry.coordinates。");
      return;
    }
    const xs = coordinates.map((item) => item[0]);
    const ys = coordinates.map((item) => item[1]);
    const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys);
    const width = 1000, height = 600, pad = 55;
    const sx = (width - pad * 2) / Math.max(1e-6, maxX - minX);
    const sy = (height - pad * 2) / Math.max(1e-6, maxY - minY);
    const scale = Math.min(sx, sy);
    const xOffset = (width - (maxX - minX) * scale) / 2;
    const yOffset = (height - (maxY - minY) * scale) / 2;
    const project = (point) => [xOffset + (point[0] - minX) * scale, height - yOffset - (point[1] - minY) * scale];
    const flowByDept = Object.fromEntries((flow || []).map((item) => [item.deptID, item]));
    let shapes = "", points = "";

    function linePath(line, close) {
      return line.map((point, index) => {
        const projected = project(point);
        return (index ? "L" : "M") + projected[0].toFixed(1) + "," + projected[1].toFixed(1);
      }).join(" ") + (close ? " Z" : "");
    }

    (geojson.features || []).forEach((feature) => {
      const geometry = feature.geometry || {};
      const props = feature.properties || {};
      if (geometry.type === "Polygon") {
        const path = geometry.coordinates.map((ring) => linePath(ring, true)).join(" ");
        const isRoom = props.featureType === "room" || props.featureType === "departmentArea";
        shapes += '<path d="' + path + '" fill="' + (isRoom ? "#dceceb" : "#eef3f4") + '" stroke="#9eb5bb" stroke-width="2" fill-rule="evenodd"/>';
      } else if (geometry.type === "MultiPolygon") {
        geometry.coordinates.forEach((polygon) => {
          shapes += '<path d="' + polygon.map((ring) => linePath(ring, true)).join(" ") + '" fill="#eef3f4" stroke="#9eb5bb" stroke-width="2" fill-rule="evenodd"/>';
        });
      } else if (geometry.type === "LineString") {
        shapes += '<path d="' + linePath(geometry.coordinates, false) + '" fill="none" stroke="' +
          (props.featureType === "route" ? "#77aeb1" : "#aebfc4") + '" stroke-width="' +
          (props.featureType === "route" ? "6" : "3") + '" stroke-linecap="round" opacity=".8"/>';
      } else if (geometry.type === "Point") {
        const projected = project(geometry.coordinates);
        const dept = flowByDept[props.deptID] || null;
        const count = dept ? dept.peopleFlow : 0;
        const color = count >= 20 ? "#d5524a" : count >= 8 ? "#e8a838" : "#3fa77c";
        const radius = 10 + Math.min(22, Math.sqrt(count) * 4);
        const label = (dept && dept.deptName) || props.name || "";
        points += '<g transform="translate(' + projected[0].toFixed(1) + ' ' + projected[1].toFixed(1) +
          ')"><circle r="' + (radius + 7) + '" fill="' + color + '" opacity=".13"/><circle r="' + radius +
          '" fill="' + color + '" opacity=".88" stroke="white" stroke-width="4"/><text y="4" text-anchor="middle" fill="white" font-size="12" font-weight="800">' +
          count + '</text><text y="' + (radius + 22) + '" text-anchor="middle" fill="#24394a" font-size="13" font-weight="700">' +
          escapeHtml(label) + '</text></g>';
      }
    });
    container.innerHTML = '<svg viewBox="0 0 1000 600" role="img" aria-label="医院楼层地图"><g>' + shapes + points + '</g></svg>';
  }

  function renderDepartments() {
    const target = byId("departmentTable");
    if (!state.departments.length) {
      target.innerHTML = emptyState("还没有科室", "点击右上角“新增科室”建立医院基础信息");
      return;
    }
    target.innerHTML = '<table class="data-table"><thead><tr><th>科室</th><th>位置</th><th>开放时间</th><th>容量</th><th>状态</th><th>操作</th></tr></thead><tbody>' +
      state.departments.map((item) => '<tr><td><b>' + escapeHtml(item.deptName) + '</b></td><td>' +
        escapeHtml(item.location || "—") + '</td><td>' + item.openTimeStart + '–' + item.openTimeEnd +
        '</td><td>' + item.capacity + '</td><td><span class="status-pill' + (item.isAvailable ? '' : ' off') + '">' +
        (item.isAvailable ? '正常开放' : '暂停开放') + '</span></td><td><div class="table-actions"><button data-edit-dept="' +
        item.deptID + '">编辑</button><button class="delete" data-delete-dept="' + item.deptID + '">删除</button></div></td></tr>').join("") +
      '</tbody></table>';
    target.querySelectorAll("[data-edit-dept]").forEach((button) => button.addEventListener("click", () => {
      openDepartment(state.departments.find((item) => item.deptID === button.dataset.editDept));
    }));
    target.querySelectorAll("[data-delete-dept]").forEach((button) => button.addEventListener("click", () => deleteDepartment(button.dataset.deleteDept)));
  }

  byId("addDepartment").addEventListener("click", () => openDepartment(null));

  function openDepartment(item) {
    byId("dialogTitle").textContent = item ? "编辑科室" : "新增科室";
    dialogBody.innerHTML = '<form id="recordForm" class="dialog-grid">' +
      field("科室名称", "deptName", item && item.deptName, "text", true) +
      field("位置描述", "location", item && item.location) +
      field("开放开始", "openTimeStart", item ? item.openTimeStart : "08:00", "time", true) +
      field("开放结束", "openTimeEnd", item ? item.openTimeEnd : "17:00", "time", true) +
      field("设备/检查位容量", "capacity", item ? item.capacity : 1, "number", true, 'min="1"') +
      '<label>当前状态<select name="isAvailable"><option value="true"' + (!item || item.isAvailable ? ' selected' : '') +
      '>正常开放</option><option value="false"' + (item && !item.isAvailable ? ' selected' : '') + '>暂停开放</option></select></label>' +
      dialogActions() + '</form>';
    byId("recordForm").addEventListener("submit", async (event) => {
      event.preventDefault();
      const payload = formObject(event.currentTarget);
      payload.capacity = Number(payload.capacity);
      payload.isAvailable = payload.isAvailable === "true";
      try {
        await api(item ? "/departments/" + item.deptID : "/departments", { method: item ? "PATCH" : "POST", body: payload });
        dialog.close();
        await loadWorkspace();
        toast(item ? "科室已更新" : "科室已创建");
      } catch (error) { toast(error.message, "error"); }
    });
    dialog.showModal();
  }

  async function deleteDepartment(id) {
    if (!confirm("确定删除这个科室吗？已有检查项目或历史记录时系统会阻止删除。")) return;
    try {
      await api("/departments/" + id, { method: "DELETE" });
      await loadWorkspace();
      toast("科室已删除");
    } catch (error) { toast(error.message, "error"); }
  }

  function renderExams() {
    const target = byId("examTable");
    const deptNames = Object.fromEntries(state.departments.map((item) => [item.deptID, item.deptName]));
    if (!state.exams.length) {
      target.innerHTML = emptyState("还没有检查项目", "先创建科室，再添加该科室可执行的检查项目");
      return;
    }
    target.innerHTML = '<table class="data-table"><thead><tr><th>项目</th><th>所属科室</th><th>标准耗时</th><th>优先级</th><th>约束</th><th>状态</th><th>操作</th></tr></thead><tbody>' +
      state.exams.map((item) => '<tr><td><b>' + escapeHtml(item.itemName) + '</b>' + (item.isCritical ? '<br><small>关键路径</small>' : '') +
        '</td><td>' + escapeHtml(deptNames[item.deptID] || "未知科室") + '</td><td>' + item.duration + ' 分钟</td><td>' +
        item.priority + '</td><td>' + Object.keys(item.prerequisites || {}).length + ' 项 / ' + item.conflicts.length +
        ' 个互斥</td><td><span class="status-pill' + (item.isActive ? '' : ' off') + '">' + (item.isActive ? '启用' : '停用') +
        '</span></td><td><div class="table-actions"><button data-edit-exam="' + item.itemID + '">编辑</button><button class="delete" data-delete-exam="' +
        item.itemID + '">删除</button></div></td></tr>').join("") + '</tbody></table>';
    target.querySelectorAll("[data-edit-exam]").forEach((button) => button.addEventListener("click", () => {
      openExam(state.exams.find((item) => item.itemID === button.dataset.editExam));
    }));
    target.querySelectorAll("[data-delete-exam]").forEach((button) => button.addEventListener("click", () => deleteExam(button.dataset.deleteExam)));
  }

  byId("addExam").addEventListener("click", () => {
    if (!state.departments.length) return toast("请先创建科室", "error");
    openExam(null);
  });

  function openExam(item) {
    byId("dialogTitle").textContent = item ? "编辑检查项目" : "新增检查项目";
    const deptOptions = state.departments.map((dept) => '<option value="' + dept.deptID + '"' +
      (item && item.deptID === dept.deptID ? ' selected' : '') + '>' + escapeHtml(dept.deptName) + '</option>').join("");
    dialogBody.innerHTML = '<form id="recordForm" class="dialog-grid"><label>所属科室<select name="deptID">' + deptOptions +
      '</select></label>' + field("项目名称", "itemName", item && item.itemName, "text", true) +
      field("标准耗时（分钟）", "duration", item ? item.duration : 10, "number", true, 'min="1"') +
      field("医疗优先级（0–100）", "priority", item ? item.priority : 0, "number", true, 'min="0" max="100"') +
      '<label>关键路径<select name="isCritical"><option value="false"' + (!item || !item.isCritical ? ' selected' : '') +
      '>否</option><option value="true"' + (item && item.isCritical ? ' selected' : '') + '>是</option></select></label>' +
      '<label>当前状态<select name="isActive"><option value="true"' + (!item || item.isActive ? ' selected' : '') +
      '>启用</option><option value="false"' + (item && !item.isActive ? ' selected' : '') + '>停用</option></select></label>' +
      textareaField("前置约束 JSON", "prerequisites", JSON.stringify(item ? item.prerequisites : {}, null, 2)) +
      textareaField("允许时段 JSON", "allowedTimeSlots", JSON.stringify(item ? item.allowedTimeSlots : {}, null, 2)) +
      textareaField("互斥项目 ID（每行一个）", "conflicts", item ? item.conflicts.join("\\n") : "") +
      dialogActions() + '</form>';
    byId("recordForm").addEventListener("submit", async (event) => {
      event.preventDefault();
      const data = formObject(event.currentTarget);
      try {
        const payload = {
          deptID: data.deptID,
          itemName: data.itemName,
          duration: Number(data.duration),
          priority: Number(data.priority),
          isCritical: data.isCritical === "true",
          isActive: data.isActive === "true",
          prerequisites: JSON.parse(data.prerequisites || "{}"),
          allowedTimeSlots: JSON.parse(data.allowedTimeSlots || "{}"),
          conflicts: data.conflicts.split(/\\r?\\n|,/).map((value) => value.trim()).filter(Boolean)
        };
        await api(item ? "/exams/" + item.itemID : "/exams", { method: item ? "PATCH" : "POST", body: payload });
        dialog.close();
        await loadWorkspace();
        toast(item ? "项目已更新" : "项目已创建");
      } catch (error) { toast(error instanceof SyntaxError ? "JSON 格式不正确" : error.message, "error"); }
    });
    dialog.showModal();
  }

  async function deleteExam(id) {
    if (!confirm("确定删除这个检查项目吗？已有执行记录时请改为停用。")) return;
    try {
      await api("/exams/" + id, { method: "DELETE" });
      await loadWorkspace();
      toast("项目已删除");
    } catch (error) { toast(error.message, "error"); }
  }

  function renderPackages() {
    const target = byId("packageTable");
    if (!state.packages.length) {
      target.innerHTML = emptyState("还没有体检套餐", "选择本医院检查项目创建套餐，上架后会自动显示在患者小程序");
      return;
    }
    target.innerHTML = '<table class="data-table"><thead><tr><th>套餐</th><th>类型</th><th>价格</th><th>项目数</th><th>预计耗时</th><th>小程序状态</th><th>操作</th></tr></thead><tbody>' +
      state.packages.map((item) => '<tr><td><b>' + escapeHtml(item.packageName) + '</b>' +
        (item.tag ? '<br><small>' + escapeHtml(item.tag) + '</small>' : '') + '</td><td>' +
        escapeHtml(item.packageType) + '</td><td>' + (item.price > 0 ? '¥' + Number(item.price).toFixed(2) : '以医院为准') +
        '</td><td>' + item.includedItemIDs.length + ' 项</td><td>' + item.defaultDuration +
        ' 分钟</td><td><span class="status-pill' + (item.isPublished ? '' : ' off') + '">' +
        (item.isPublished ? '已上架' : '草稿/已下架') + '</span></td><td><div class="table-actions"><button data-edit-package="' +
        item.packageID + '">编辑</button><button class="delete" data-delete-package="' + item.packageID +
        '">删除</button></div></td></tr>').join("") + '</tbody></table>';
    target.querySelectorAll("[data-edit-package]").forEach((button) => button.addEventListener("click", () => {
      openPackage(state.packages.find((item) => item.packageID === button.dataset.editPackage));
    }));
    target.querySelectorAll("[data-delete-package]").forEach((button) => button.addEventListener("click", () => deletePackage(button.dataset.deletePackage)));
  }

  byId("addPackage").addEventListener("click", () => {
    if (!state.exams.length) return toast("请先创建检查项目", "error");
    openPackage(null);
  });

  function openPackage(item) {
    byId("dialogTitle").textContent = item ? "编辑体检套餐" : "新增体检套餐";
    const selected = new Set(item ? item.includedItemIDs : []);
    const deptNames = Object.fromEntries(state.departments.map((dept) => [dept.deptID, dept.deptName]));
    const examOptions = state.exams.map((exam) => '<label class="check-option"><input type="checkbox" name="includedItemIDs" value="' +
      exam.itemID + '"' + (selected.has(exam.itemID) ? ' checked' : '') + ' /><span><b>' + escapeHtml(exam.itemName) +
      '</b><small>' + escapeHtml(deptNames[exam.deptID] || "未知科室") + ' · ' + exam.duration + ' 分钟' +
      (exam.isActive ? '' : ' · 已停用') + '</small></span></label>').join("");
    dialogBody.innerHTML = '<form id="recordForm" class="dialog-grid">' +
      field("套餐名称", "packageName", item && item.packageName, "text", true) +
      field("套餐类型", "packageType", item ? item.packageType : "健康体检", "text", true) +
      field("价格（元，0 表示以医院为准）", "price", item ? item.price : 0, "number", true, 'min="0" step="0.01"') +
      field("展示标签", "tag", item && item.tag) +
      field("预计总耗时（分钟，0 自动计算）", "defaultDuration", item ? item.defaultDuration : 0, "number", true, 'min="0"') +
      '<label>小程序状态<select name="isPublished"><option value="false"' + (!item || !item.isPublished ? ' selected' : '') +
      '>保存为草稿/下架</option><option value="true"' + (item && item.isPublished ? ' selected' : '') + '>立即上架</option></select></label>' +
      textareaField("套餐说明", "description", item && item.description) +
      textareaField("适用人群（每行一项）", "suitable", item ? item.suitable.join("\n") : "") +
      textareaField("注意事项（每行一项）", "notice", item ? item.notice.join("\n") : "") +
      '<div class="full"><span class="field-label">包含的检查项目</span><div class="check-grid">' + examOptions + '</div></div>' +
      dialogActions() + '</form>';
    byId("recordForm").addEventListener("submit", async (event) => {
      event.preventDefault();
      const data = formObject(event.currentTarget);
      const includedItemIDs = Array.from(event.currentTarget.querySelectorAll('[name="includedItemIDs"]:checked')).map((input) => input.value);
      if (!includedItemIDs.length) return toast("请至少选择一个检查项目", "error");
      const lines = (value) => value.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
      const payload = {
        packageName: data.packageName,
        packageType: data.packageType,
        price: Number(data.price),
        tag: data.tag,
        description: data.description,
        includedItemIDs: includedItemIDs,
        defaultDuration: Number(data.defaultDuration),
        suitable: lines(data.suitable),
        notice: lines(data.notice),
        isPublished: data.isPublished === "true"
      };
      try {
        await api(item ? "/packages/" + item.packageID : "/packages", { method: item ? "PATCH" : "POST", body: payload });
        dialog.close();
        await loadWorkspace();
        toast(payload.isPublished ? "套餐已保存并上架" : "套餐草稿已保存");
      } catch (error) { toast(error.message, "error"); }
    });
    dialog.showModal();
  }

  async function deletePackage(id) {
    if (!confirm("确定删除这个套餐吗？已有体检计划时请改为下架。")) return;
    try {
      await api("/packages/" + id, { method: "DELETE" });
      await loadWorkspace();
      toast("套餐已删除");
    } catch (error) { toast(error.message, "error"); }
  }

  function field(label, name, value, type, required, extra) {
    return '<label>' + label + '<input name="' + name + '" type="' + (type || "text") + '" value="' +
      escapeHtml(value == null ? "" : value) + '"' + (required ? " required" : "") + " " + (extra || "") + ' /></label>';
  }

  function textareaField(label, name, value) {
    return '<label class="full">' + label + '<textarea name="' + name + '" rows="4">' + escapeHtml(value || "") + '</textarea></label>';
  }

  function dialogActions() {
    return '<div class="dialog-actions"><button type="button" class="secondary-button" id="cancelDialog">取消</button><button type="submit" class="primary-button">保存</button></div>';
  }

  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) dialog.close();
    if (event.target.id === "cancelDialog") dialog.close();
  });

  byId("workspaceImportForm").querySelector('[name="workspaceFile"]').addEventListener("change", async (event) => {
    const file = event.currentTarget.files[0];
    workspaceImportPayload = null;
    byId("importResult").classList.add("hidden");
    if (!file) {
      byId("importFileSummary").innerHTML = "<span>尚未选择文件</span>";
      return;
    }
    try {
      const payload = JSON.parse(await file.text());
      const sections = ["departments", "exams", "packages", "gis"];
      sections.forEach((name) => {
        if (payload[name] != null && !Array.isArray(payload[name])) throw new Error(name + " 必须是数组");
      });
      workspaceImportPayload = payload;
      byId("importFileSummary").innerHTML = '<b>' + escapeHtml(file.name) + '</b><div class="import-counts">' +
        '<span>科室 ' + (payload.departments || []).length + '</span><span>项目 ' + (payload.exams || []).length +
        '</span><span>套餐 ' + (payload.packages || []).length + '</span><span>GIS ' + (payload.gis || []).length +
        '</span></div><small>格式版本 ' + escapeHtml(payload.formatVersion || "未填写") + '</small>';
    } catch (error) {
      event.currentTarget.value = "";
      byId("importFileSummary").innerHTML = "<span>文件解析失败，请重新选择</span>";
      toast(error instanceof SyntaxError ? "文件不是有效的 JSON" : error.message, "error");
    }
  });

  byId("workspaceImportForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!workspaceImportPayload) return toast("请先选择有效的标准 JSON 文件", "error");
    const submitButton = event.currentTarget.querySelector('[type="submit"]');
    submitButton.disabled = true;
    submitButton.textContent = "正在校验并导入…";
    try {
      const result = await api("/imports/workspace", { method: "POST", body: workspaceImportPayload });
      await loadWorkspace();
      const summary = result.summary;
      const summaryText = (name) => summary[name].created + " 新增 / " + summary[name].updated + " 更新";
      byId("importResult").innerHTML = '<b>导入成功</b><div class="import-result-grid"><span>科室<small>' +
        summaryText("departments") + '</small></span><span>项目<small>' + summaryText("exams") +
        '</small></span><span>套餐<small>' + summaryText("packages") + '</small></span><span>GIS<small>' +
        summaryText("gis") + '</small></span></div>';
      byId("importResult").classList.remove("hidden");
      event.currentTarget.reset();
      workspaceImportPayload = null;
      byId("importFileSummary").innerHTML = "<span>导入完成，可继续选择其他文件</span>";
      toast("医院数据已完成一键导入");
    } catch (error) {
      toast(error.message, "error");
    } finally {
      submitButton.disabled = false;
      submitButton.textContent = "一键校验并导入";
    }
  });

  byId("downloadImportTemplate").addEventListener("click", async () => {
    try {
      const template = await api("/imports/template");
      const blob = new Blob([JSON.stringify(template, null, 2)], { type: "application/json;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = "hospital-workspace-template.json";
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
      toast("标准模板已下载");
    } catch (error) { toast(error.message, "error"); }
  });

  byId("gisForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const file = data.get("gisFile");
    try {
      const geojson = JSON.parse(await file.text());
      await api("/gis/" + encodeURIComponent(data.get("floorKey")), { method: "PUT", body: { geojson: geojson } });
      event.currentTarget.reset();
      await loadWorkspace();
      toast("GIS 地图已发布新版本");
    } catch (error) { toast(error instanceof SyntaxError ? "文件不是有效的 JSON" : error.message, "error"); }
  });

  function renderGisVersions() {
    byId("gisVersions").innerHTML = state.gis.length ? state.gis.map((item) =>
      '<div class="version-item"><div><b>' + escapeHtml(item.floorKey) + '</b><br><small>版本 ' + item.version + ' · ' +
      formatTime(item.updateTime) + '</small></div><button data-preview-floor="' + escapeHtml(item.floorKey) + '">预览</button></div>'
    ).join("") : emptyState("暂无地图版本", "上传后会保留楼层版本号与更新时间");
    byId("gisVersions").querySelectorAll("[data-preview-floor]").forEach((button) => button.addEventListener("click", () => previewGis(button.dataset.previewFloor)));
    if (state.gis.length && !byId("gisPreview").querySelector("svg")) previewGis(state.gis[0].floorKey);
    if (!state.gis.length) renderMapEmpty(byId("gisPreview"), "等待 GIS 文件", "上传 GeoJSON 后可在这里检查楼层轮廓和科室点位。");
  }

  async function previewGis(floor) {
    try {
      const data = await api("/gis/" + encodeURIComponent(floor));
      byId("gisPreviewMeta").textContent = floor + " · 版本 " + data.version + " · " + formatTime(data.updateTime);
      renderMap(byId("gisPreview"), data.geojson, state.dashboard ? state.dashboard.flow : []);
    } catch (error) { toast(error.message, "error"); }
  }

  function renderAdjustmentOptions() {
    const deptOptions = state.departments.map((item) => '<option value="' + item.deptID + '">' + escapeHtml(item.deptName) + '</option>').join("");
    byId("anomalyDepartment").innerHTML = deptOptions || '<option value="">请先创建科室</option>';
    const deptNames = Object.fromEntries(state.departments.map((item) => [item.deptID, item.deptName]));
    byId("queueExam").innerHTML = state.exams.map((item) => '<option value="' + item.itemID + '">' +
      escapeHtml(item.itemName) + ' · ' + escapeHtml(deptNames[item.deptID] || "") + '</option>').join("") ||
      '<option value="">请先创建检查项目</option>';
  }

  byId("anomalyForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await api("/anomalies", { method: "POST", body: formObject(event.currentTarget) });
      event.currentTarget.reset();
      await loadWorkspace();
      toast("现场异常已上报");
    } catch (error) { toast(error.message, "error"); }
  });

  byId("queueForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const data = formObject(event.currentTarget);
    try {
      await api("/queues", {
        method: "POST",
        body: {
          itemID: data.itemID,
          queueCount: Number(data.queueCount),
          estimatedWaitTime: Math.round(Number(data.waitMinutes) * 60),
          validMinutes: 30
        }
      });
      await loadWorkspace();
      toast("排队快照已更新");
    } catch (error) { toast(error.message, "error"); }
  });

  function renderAnomalies() {
    byId("anomalyList").innerHTML = state.anomalies.length ? state.anomalies.map((item) =>
      '<div class="anomaly-card' + (item.isResolved ? ' resolved' : '') + '"><span class="severity"></span><div><h4>' +
      escapeHtml(item.anomalyType) + ' · ' + escapeHtml(item.deptName || "") + '</h4><p>' +
      escapeHtml(item.description || "未填写详细说明") + '</p><time>' + formatTime(item.reportTime) +
      '</time></div>' + (item.isResolved ? '<span class="status-pill">已解决</span>' :
      '<button data-resolve="' + item.reportID + '">标记解决</button>') + '</div>'
    ).join("") : emptyState("暂无异常记录", "现场临时变化可在左侧快速上报");
    byId("anomalyList").querySelectorAll("[data-resolve]").forEach((button) => button.addEventListener("click", async () => {
      try {
        await api("/anomalies/" + button.dataset.resolve + "/resolve", { method: "POST", body: { reopenDepartment: true } });
        await loadWorkspace();
        toast("异常已解决");
      } catch (error) { toast(error.message, "error"); }
    }));
  }

  function formatTime(value) {
    if (!value) return "—";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { hour12: false });
  }

  async function initialize() {
    try {
      state.me = await api("/auth/me");
      showApp();
      await loadWorkspace();
    } catch (_) {
      showAuth();
    }
  }

  initialize();
})();
