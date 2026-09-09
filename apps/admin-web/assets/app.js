(function () {
  "use strict";

  const state = { me: null, departments: [], exams: [], packages: [], gis: [], plans: { items: [], total: 0 }, dashboard: null, anomalies: [], demo: null };
  const byId = (id) => document.getElementById(id);
  const authView = byId("authView");
  const appView = byId("appView");
  const dialog = byId("editorDialog");
  const dialogBody = byId("dialogBody");
  let toastTimer;
  let workspaceImportPayload = null;
  let registrationWorkspacePayload = null;
  let pendingGisUpload = null;
  let hospitalCoverDataUrl = null;
  let hospitalCoverDirty = false;
  let dashboardMapRequestId = 0;
  let workspaceRequestId = 0;
  let planRequestId = 0;
  let authGeneration = 0;
  const MAX_MAP_COORDINATES = 100000;

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
    const requestAuthGeneration = authGeneration;
    const config = Object.assign({ credentials: "same-origin" }, options || {});
    if (config.body && typeof config.body !== "string") {
      config.headers = Object.assign({ "Content-Type": "application/json" }, config.headers || {});
      config.body = JSON.stringify(config.body);
    }
    const response = await fetch("/api" + path, config);
    if (response.status === 401) {
      if (requestAuthGeneration === authGeneration) showAuth();
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
    authGeneration += 1;
    workspaceRequestId += 1;
    dashboardMapRequestId += 1;
    planRequestId += 1;
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

  function downloadJson(payload, filename) {
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  }

  function syntaxLocationMessage(text, error) {
    const match = String(error && error.message || "").match(/position\s+(\d+)/i);
    if (!match) return "文件不是有效的 JSON";
    const position = Number(match[1]);
    const before = text.slice(0, position);
    const line = before.split(/\r?\n/).length;
    const column = before.length - before.lastIndexOf("\n");
    return "JSON 解析失败：第 " + line + " 行，第 " + column + " 列附近";
  }

  function formatMinutesFromSeconds(value) {
    return Math.round(Number(value || 0) / 60);
  }

  function metricDetailButton(label, value, note, key, alert) {
    return '<button type="button" class="metric-card' + (alert ? ' alert' : '') +
      '" data-metric-detail="' + escapeHtml(key) + '"><span class="metric-label">' +
      escapeHtml(label) + '</span><strong>' + escapeHtml(value) + '</strong><small>' +
      escapeHtml(note) + '</small></button>';
  }

  function validationCard(title, lines, tone) {
    return '<div class="validation-card ' + escapeHtml(tone || "ok") + '"><h4>' +
      escapeHtml(title) + '</h4><ul>' + lines.map((line) => '<li>' + escapeHtml(line) +
      '</li>').join("") + '</ul></div>';
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
      const me = await api("/auth/login", { method: "POST", body: formObject(event.currentTarget) });
      authGeneration += 1;
      workspaceRequestId += 1;
      state.me = me;
      showApp();
      await loadWorkspace();
      toast("登录成功");
    } catch (error) { toast(error.message, "error"); }
  });

  byId("registerForm").querySelector('[name="workspaceFile"]').addEventListener("change", async (event) => {
    const input = event.currentTarget;
    const file = input.files[0];
    registrationWorkspacePayload = null;
    if (!file) {
      byId("registerWorkspaceSummary").innerHTML = "<span>尚未选择注册数据包</span>";
      return;
    }
    const text = await file.text();
    try {
      const payload = JSON.parse(text);
      const sections = ["departments", "exams", "packages", "gis"];
      if (!payload.hospital) throw new Error("注册数据缺少 hospital 医院信息");
      sections.forEach((name) => {
        if (!Array.isArray(payload[name]) || !payload[name].length) throw new Error("注册数据中的 " + name + " 不能为空");
      });
      registrationWorkspacePayload = payload;
      byId("registerWorkspaceSummary").innerHTML = '<b>' + escapeHtml(payload.hospital.hospitalName || file.name) +
        '</b><div class="import-counts"><span>科室 ' + payload.departments.length + '</span><span>项目 ' +
        payload.exams.length + '</span><span>套餐 ' + payload.packages.length + '</span><span>GIS ' +
        payload.gis.length + '</span></div>';
    } catch (error) {
      input.value = "";
      byId("registerWorkspaceSummary").innerHTML = "<span>文件解析失败，请重新选择</span>";
      toast(error instanceof SyntaxError ? syntaxLocationMessage(text, error) : error.message, "error");
    }
  });

  byId("downloadRegisterTemplate").addEventListener("click", async () => {
    try {
      downloadJson(await api("/auth/register-template"), "hospital-registration-workspace.json");
      toast("注册数据模板已下载");
    } catch (error) { toast(error.message, "error"); }
  });

  byId("registerForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!registrationWorkspacePayload) return toast("请先选择有效的完整医院数据包", "error");
    const payload = formObject(event.currentTarget);
    delete payload.workspaceFile;
    payload.workspace = registrationWorkspacePayload;
    const submitButton = event.currentTarget.querySelector('[type="submit"]');
    submitButton.disabled = true;
    submitButton.textContent = "正在创建并准备患者池…";
    try {
      const me = await api("/auth/register", { method: "POST", body: payload });
      authGeneration += 1;
      workspaceRequestId += 1;
      state.me = me;
      showApp();
      await loadWorkspace();
      registrationWorkspacePayload = null;
      toast("医院账号、完整数据和 100 人演示池已创建");
    } catch (error) { toast(error.message, "error"); }
    finally {
      submitButton.disabled = false;
      submitButton.textContent = "创建医院账号";
    }
  });

  byId("logoutButton").addEventListener("click", async () => {
    try {
      await api("/auth/logout", { method: "POST" });
      showAuth();
    } catch (error) {
      // A 401 already cleared the local view in api(). On transport/server
      // errors the HttpOnly session cookie may still be valid, so keep the
      // authenticated view instead of claiming the logout succeeded.
      if (state.me) toast("退出失败：" + error.message, "error");
    }
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
    const requestId = ++workspaceRequestId;
    const currentPlanRequestId = ++planRequestId;
    const me = await api("/auth/me");
    const results = await Promise.all([
      api("/departments"),
      api("/exams"),
      api("/packages"),
      api("/gis"),
      api(planQueryPath()),
      api("/dashboard/summary"),
      api("/anomalies"),
      me.user.isOwner ? api("/demo-patients") : Promise.resolve(null)
    ]);
    if (requestId !== workspaceRequestId || currentPlanRequestId !== planRequestId) return;
    state.me = me;
    byId("hospitalName").textContent = me.hospital.hospitalName;
    byId("adminName").textContent = me.user.name;
    byId("adminPhone").textContent = me.user.phone;
    byId("avatar").textContent = me.user.name.slice(0, 1);
    state.departments = results[0];
    state.exams = results[1];
    state.packages = results[2];
    state.gis = results[3];
    state.plans = results[4];
    state.dashboard = results[5];
    state.anomalies = results[6];
    state.demo = results[7];
    byId("demoPatientTrigger").classList.toggle(
      "hidden", !state.me.user.isOwner
    );
    renderEverything();
  }

  function renderEverything() {
    renderHospitalSettings();
    renderDashboard();
    renderPlans();
    renderDepartments();
    renderExams();
    renderPackages();
    renderGisVersions();
    renderAdjustmentOptions();
    renderAnomalies();
  }

  function planQueryPath() {
    const date = byId("planDate").value || "today";
    const status = byId("planStatus").value || "all";
    const query = byId("planQuery").value.trim();
    return "/plans?date=" + encodeURIComponent(date) + "&status=" + encodeURIComponent(status) +
      "&query=" + encodeURIComponent(query) + "&limit=200";
  }

  async function loadPlans() {
    const requestId = ++planRequestId;
    const plans = await api(planQueryPath());
    if (requestId !== planRequestId) return;
    state.plans = plans;
    renderPlans();
  }

  byId("planFilterForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = event.currentTarget.querySelector('[type="submit"]');
    button.disabled = true;
    try {
      await loadPlans();
    } catch (error) {
      toast(error.message, "error");
    } finally {
      button.disabled = false;
    }
  });

  function renderHospitalCover(value) {
    const preview = byId("hospitalCoverPreview");
    const frame = preview.parentElement;
    if (value) preview.src = value;
    else preview.removeAttribute("src");
    preview.classList.toggle("hidden", !value);
    frame.classList.toggle("empty", !value);
  }

  function renderHospitalSettings() {
    if (!state.me || !state.me.hospital) return;
    const hospital = state.me.hospital;
    const form = byId("hospitalSettingsForm");
    form.elements.hospitalName.value = hospital.hospitalName || "";
    form.elements.hospitalLevel.value = hospital.hospitalLevel || "未定级";
    form.elements.positioning.value = hospital.positioning || "综合医疗机构";
    form.elements.address.value = hospital.address || "";
    form.elements.openTime.value = hospital.openTime || "08:00-17:00";
    form.elements.isAvailable.value = hospital.isAvailable === false ? "false" : "true";
    form.elements.appointmentSlotMinutes.value = hospital.appointmentSlotMinutes || 30;
    form.elements.appointmentSlotCapacity.value = hospital.appointmentSlotCapacity || 20;
    form.elements.appointmentDaysAhead.value = hospital.appointmentDaysAhead || 7;
    hospitalCoverDataUrl = hospital.coverImageUrl || null;
    hospitalCoverDirty = false;
    byId("hospitalCoverFile").value = "";
    renderHospitalCover(hospitalCoverDataUrl);
  }

  byId("hospitalCoverFile").addEventListener("change", (event) => {
    const file = event.currentTarget.files[0];
    if (!file) return;
    if (!["image/jpeg", "image/png", "image/webp"].includes(file.type)) {
      event.currentTarget.value = "";
      return toast("请选择 JPEG、PNG 或 WebP 图片", "error");
    }
    if (file.size > 1024 * 1024) {
      event.currentTarget.value = "";
      return toast("医院图片不能超过 1 MB", "error");
    }
    const reader = new FileReader();
    reader.onload = () => {
      hospitalCoverDataUrl = String(reader.result || "");
      hospitalCoverDirty = true;
      renderHospitalCover(hospitalCoverDataUrl);
    };
    reader.onerror = () => toast("图片读取失败，请重新选择", "error");
    reader.readAsDataURL(file);
  });

  byId("removeHospitalCover").addEventListener("click", () => {
    hospitalCoverDataUrl = null;
    hospitalCoverDirty = true;
    byId("hospitalCoverFile").value = "";
    renderHospitalCover(null);
  });

  byId("hospitalSettingsForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const data = formObject(event.currentTarget);
    const payload = {
      hospitalName: data.hospitalName,
      hospitalLevel: data.hospitalLevel,
      positioning: data.positioning,
      address: data.address,
      openTime: data.openTime,
      isAvailable: data.isAvailable === "true",
      appointmentSlotMinutes: Number(data.appointmentSlotMinutes),
      appointmentSlotCapacity: Number(data.appointmentSlotCapacity),
      appointmentDaysAhead: Number(data.appointmentDaysAhead)
    };
    if (hospitalCoverDirty) payload.coverImageUrl = hospitalCoverDataUrl;
    const button = event.currentTarget.querySelector('[type="submit"]');
    button.disabled = true;
    try {
      state.me.hospital = await api("/hospital", { method: "PATCH", body: payload });
      byId("hospitalName").textContent = state.me.hospital.hospitalName;
      renderHospitalSettings();
      toast("医院设置已保存");
    } catch (error) { toast(error.message, "error"); }
    finally { button.disabled = false; }
  });

  function renderDashboard() {
    const metrics = state.dashboard.metrics;
    const flow = state.dashboard.flow || [];
    const closedCount = state.departments.filter((item) => !item.isAvailable).length;
    const crowdedCount = flow.filter((item) =>
      item.peopleFlow >= 8 || formatMinutesFromSeconds(item.estimatedWaitTime) >= 30
    ).length;
    const maxWait = flow.reduce((current, item) =>
      Math.max(current, formatMinutesFromSeconds(item.estimatedWaitTime)), 0);

    renderPriorityBoard(flow, closedCount, metrics.unresolvedAnomalies);
    byId("metricGrid").innerHTML =
      metricDetailButton("拥堵科室", crowdedCount, "等待较长或现场人流繁忙", "crowded", crowdedCount > 0) +
      metricDetailButton("暂停开放", closedCount, metrics.openDepartments + "/" + metrics.departmentCount + " 科室开放", "closed", closedCount > 0) +
      metricDetailButton("未解决异常", metrics.unresolvedAnomalies, "来自现场异常上报", "anomalies", metrics.unresolvedAnomalies > 0) +
      metricDetailButton("今日体检", metrics.todayPlans, metrics.inProgressPlans + " 人正在体检", "plans") +
      metricDetailButton("平均等待", formatMinutesFromSeconds(metrics.averageWaitSeconds) + " 分钟", "按系统内当前计划估算", "averageWait") +
      metricDetailButton("最长等待", maxWait + " 分钟", "按当前科室队列计算", "maxWait", maxWait >= 30);
    byId("generatedAt").textContent = "更新于 " + formatTime(state.dashboard.generatedAt);
    byId("metricGrid").querySelectorAll("[data-metric-detail]").forEach((button) => {
      button.addEventListener("click", () => openMetricDetail(button.dataset.metricDetail));
    });
    renderFlowList(flow);
    const floorSelect = byId("dashboardFloor");
    const previous = floorSelect.value;
    floorSelect.innerHTML = state.gis.length
      ? state.gis.map((item) => '<option value="' + escapeHtml(item.floorKey) + '">' + escapeHtml(item.floorKey) + '</option>').join("")
      : '<option value="">尚无地图</option>';
    if (state.gis.some((item) => item.floorKey === previous)) floorSelect.value = previous;
    if (floorSelect.value) loadDashboardMap(floorSelect.value);
    else {
      dashboardMapRequestId += 1;
      renderMapEmpty(byId("dashboardMap"), "尚未上传院内 GIS", "前往“院内 GIS”上传 GeoJSON 后，人流会自动显示在地图上。");
    }
  }

  function renderPriorityBoard(flow, closedCount, unresolvedCount) {
    const crowded = flow.filter((item) => item.peopleFlow > 0 || item.estimatedWaitTime > 0).sort((a, b) =>
      b.estimatedWaitTime - a.estimatedWaitTime || b.peopleFlow - a.peopleFlow
    )[0];
    const closed = state.departments.filter((item) => !item.isAvailable).slice(0, 2);
    const latestAnomaly = state.anomalies.find((item) => !item.isResolved);
    byId("priorityBoard").innerHTML =
      '<article class="priority-card"><header><h3>当前最拥堵</h3><span class="priority-tag ' +
      (crowded && formatMinutesFromSeconds(crowded.estimatedWaitTime) >= 30 ? 'warn' : '') + '">' +
      (crowded ? formatMinutesFromSeconds(crowded.estimatedWaitTime) + ' 分钟' : '暂无') +
      '</span></header><p>' + (crowded ? escapeHtml(crowded.deptName) + '，现场 ' + crowded.peopleFlow +
      ' 人。' : '当前没有有效排队数据。') + '</p></article>' +
      '<article class="priority-card"><header><h3>暂停开放</h3><span class="priority-tag ' +
      (closedCount ? 'danger' : '') + '">' + closedCount + ' 个</span></header><p>' +
      (closed.length ? escapeHtml(closed.map((item) => item.deptName).join('、')) + '，请确认恢复时间。' :
      '全部科室正常开放。') + '</p></article>' +
      '<article class="priority-card"><header><h3>待处理异常</h3><span class="priority-tag ' +
      (unresolvedCount ? 'danger' : '') + '">' + unresolvedCount + ' 条</span></header><p>' +
      (latestAnomaly ? escapeHtml(latestAnomaly.anomalyType + ' · ' + (latestAnomaly.deptName || '未关联科室')) +
      '，上报于 ' + formatTime(latestAnomaly.reportTime) + '。' : '当前没有未解决异常。') + '</p></article>';
  }

  function openMetricDetail(key) {
    const metrics = state.dashboard.metrics;
    const flow = state.dashboard.flow || [];
    const closed = state.departments.filter((item) => !item.isAvailable);
    const crowded = flow.filter((item) =>
      item.peopleFlow >= 8 || formatMinutesFromSeconds(item.estimatedWaitTime) >= 30
    );
    const titleByKey = {
      crowded: "拥堵科室明细",
      closed: "暂停开放科室",
      anomalies: "未解决异常",
      plans: "今日体检情况",
      averageWait: "各科室等待时间",
      maxWait: "最长等待排行"
    };
    const linesByKey = {
      crowded: crowded.map((item) => item.deptName + "：" + item.peopleFlow + " 人，预计等待 " +
        formatMinutesFromSeconds(item.estimatedWaitTime) + " 分钟"),
      closed: closed.map((item) => item.deptName + "：" + (item.location || "未设置位置") +
        "，开放时间 " + (item.openTimeStart || "—") + "–" + (item.openTimeEnd || "—")),
      anomalies: state.anomalies.filter((item) => !item.isResolved).map((item) =>
        item.anomalyType + " · " + (item.deptName || "未关联科室") + " · " + formatTime(item.reportTime)),
      plans: ["今日计划 " + metrics.todayPlans + " 人", "进行中 " + metrics.inProgressPlans + " 人",
        "已完成 " + metrics.completedPlans + " 人"],
      averageWait: flow.map((item) => item.deptName + "：" +
        formatMinutesFromSeconds(item.estimatedWaitTime) + " 分钟"),
      maxWait: flow.slice().sort((a, b) => b.estimatedWaitTime - a.estimatedWaitTime).slice(0, 6).map((item) =>
        item.deptName + "：" + formatMinutesFromSeconds(item.estimatedWaitTime) + " 分钟")
    };
    byId("dialogTitle").textContent = titleByKey[key] || "指标明细";
    const lines = linesByKey[key] && linesByKey[key].length ? linesByKey[key] : ["暂无关联明细。"];
    dialogBody.innerHTML = '<div class="validation-panel metric-detail-panel">' +
      validationCard("数据来源", ["运行总览、系统内计划状态与异常记录；更新时间 " +
        formatTime(state.dashboard.generatedAt)], "ok") +
      validationCard("关联明细", lines, key === "closed" || key === "anomalies" ? "warn" : "ok") +
      '</div><div class="dialog-actions"><button type="button" class="primary-button" id="cancelDialog">知道了</button></div>';
    dialog.showModal();
  }

  function renderFlowList(flow) {
    const sorted = flow.slice().sort((a, b) => b.peopleFlow - a.peopleFlow);
    const max = sorted.reduce((current, item) => Math.max(current, Number(item.peopleFlow) || 0), 1);
    byId("flowList").innerHTML = sorted.length ? sorted.map((item) => {
      const wait = Math.round(item.estimatedWaitTime / 60);
      const progress = Math.max(4, item.peopleFlow / max * 100);
      return '<div class="flow-row"><header><b>' + escapeHtml(item.deptName) + '</b><b>' + item.peopleFlow +
        ' 人</b></header><progress class="progress" value="' + progress + '" max="100" aria-label="' +
        escapeHtml(item.deptName) + '人流占比"></progress><div class="flow-meta"><span>' + escapeHtml(item.location || "未设置位置") +
        '</span><span>预计等待 ' + wait + ' 分钟</span></div></div>';
    }).join("") : emptyState("暂无人流数据", "患者开始体检后将在这里自动显示");
  }

  byId("dashboardFloor").addEventListener("change", (event) => {
    if (event.target.value) loadDashboardMap(event.target.value);
  });

  async function loadDashboardMap(floor) {
    const requestId = ++dashboardMapRequestId;
    try {
      const data = await api("/dashboard/map/" + encodeURIComponent(floor));
      if (requestId !== dashboardMapRequestId || byId("dashboardFloor").value !== floor) return;
      renderMap(byId("dashboardMap"), data.geojson, data.flow);
    } catch (error) {
      if (requestId !== dashboardMapRequestId || byId("dashboardFloor").value !== floor) return;
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
    const pending = [value];
    while (pending.length) {
      const current = pending.pop();
      if (!Array.isArray(current)) continue;
      if (current.length >= 2 && Number.isFinite(current[0]) && Number.isFinite(current[1])) {
        if (result.length >= MAX_MAP_COORDINATES) return false;
        result.push([current[0], current[1]]);
        continue;
      }
      for (let index = current.length - 1; index >= 0; index -= 1) pending.push(current[index]);
    }
    return true;
  }

  function renderMap(container, geojson, flow) {
    const coordinates = [];
    const features = Array.isArray(geojson && geojson.features) ? geojson.features : [];
    for (const feature of features) {
      if (!allCoordinates(feature && feature.geometry && feature.geometry.coordinates, coordinates)) {
        renderMapEmpty(container, "地图坐标过多", "单个楼层最多显示 100000 个坐标点。");
        return;
      }
    }
    if (!coordinates.length) {
      renderMapEmpty(container, "地图没有可显示坐标", "请检查 GeoJSON geometry.coordinates。");
      return;
    }
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    coordinates.forEach((point) => {
      minX = Math.min(minX, point[0]);
      maxX = Math.max(maxX, point[0]);
      minY = Math.min(minY, point[1]);
      maxY = Math.max(maxY, point[1]);
    });
    const width = 1000, height = 600, pad = 55;
    const sx = (width - pad * 2) / Math.max(1e-6, maxX - minX);
    const sy = (height - pad * 2) / Math.max(1e-6, maxY - minY);
    const scale = Math.min(sx, sy);
    const xOffset = (width - (maxX - minX) * scale) / 2;
    const yOffset = (height - (maxY - minY) * scale) / 2;
    const project = (point) => [xOffset + (point[0] - minX) * scale, height - yOffset - (point[1] - minY) * scale];
    const flowByDept = Object.fromEntries((flow || []).map((item) => [item.deptID, item]));
    // GIS-only imports retain surveyed room references rather than business IDs.
    // Resolve only unique, explicit locations for display; never infer routes.
    function departmentForPoint(props) {
      if (props.deptID) return flowByDept[props.deptID] || null;
      const floor = String(props.floorKey || props.floor || "").toUpperCase();
      if (!floor) return null;
      const room = String(props.room_ref || "").trim();
      const matches = (flow || []).filter((item) => {
        const location = String(item.location || "").trim().toUpperCase();
        if (room) {
          const address = location.match(/^(\d+F)\s+([A-Z0-9-]+)(?=$|[\s（(；;，,])/);
          return address && address[1] === floor && address[2] === room.toUpperCase();
        }
        const locationFloor = location.match(/^\d+F(?=$|[^A-Z0-9])/);
        return locationFloor && locationFloor[0] === floor && props.name === item.deptName;
      });
      // A room repeated across buildings is ambiguous without a business binding.
      const pointMatches = features.filter((feature) => {
        const other = feature.properties || {};
        return feature.geometry && feature.geometry.type === "Point" &&
          String(other.floorKey || other.floor || "").toUpperCase() === floor &&
          (room ? String(other.room_ref || "").trim() === room : other.name === props.name);
      });
      return matches.length === 1 && pointMatches.length === 1 ? matches[0] : null;
    }
    let shapes = "", points = "";

    function linePath(line, close) {
      return line.map((point, index) => {
        const projected = project(point);
        return (index ? "L" : "M") + projected[0].toFixed(1) + "," + projected[1].toFixed(1);
      }).join(" ") + (close ? " Z" : "");
    }

    features.forEach((feature) => {
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
        const dept = departmentForPoint(props);
        const count = dept ? Math.max(0, Number(dept.peopleFlow) || 0) : 0;
        const color = !dept ? "#82939f" : count >= 20 ? "#d5524a" : count >= 8 ? "#e8a838" : "#3fa77c";
        const radius = 10 + Math.min(22, Math.sqrt(count) * 4);
        const label = (dept && dept.deptName) || props.name || "";
        points += '<g transform="translate(' + projected[0].toFixed(1) + ' ' + projected[1].toFixed(1) +
          ')"><title>' + escapeHtml(label + (dept ? "：" + count + " 人" : "：未关联科室人流")) +
          '</title><circle r="' + (radius + 7) + '" fill="' + color + '" opacity=".13"/><circle r="' + radius +
          '" fill="' + color + '" opacity=".88" stroke="white" stroke-width="4"/><text y="4" text-anchor="middle" fill="white" font-size="12" font-weight="800">' +
          (dept ? count : "—") + '</text><text y="' + (radius + 22) + '" text-anchor="middle" fill="#24394a" font-size="13" font-weight="700">' +
          escapeHtml(label) + '</text></g>';
      }
    });
    container.innerHTML = '<svg viewBox="0 0 1000 600" role="img" aria-label="医院楼层地图"><g>' + shapes + points + '</g></svg>';
  }

  function renderPlans() {
    const payload = state.plans || { items: [], total: 0 };
    const plans = Array.isArray(payload.items) ? payload.items : [];
    const scopeText = byId("planDate").value === "all" ? "全部记录" : "今日体检";
    byId("planSummary").textContent = scopeText + " · 共 " + Number(payload.total || 0) + " 人";
    const target = byId("planTable");
    if (!plans.length) {
      target.innerHTML = emptyState("没有符合条件的体检计划", "可切换日期范围或状态后重新查询");
      return;
    }
    target.innerHTML = '<table class="data-table"><thead><tr><th>患者</th><th>服务时间</th><th>套餐</th><th>当前环节</th><th>进度</th><th>状态</th><th>操作</th></tr></thead><tbody>' +
      plans.map((plan) => {
        const current = plan.currentStep;
        const statusClass = plan.status === "已完成" ? "" :
          (plan.status === "进行中" || plan.status === "待执行" ? " warn" : " off");
        return '<tr><td><span class="patient-cell"><b>' + escapeHtml(plan.patient.name) + '</b><small>' +
          escapeHtml(plan.patient.phone) + '</small></span></td><td>' + formatTime(plan.serviceAt) +
          (plan.appointmentAt ? '<br><small>预约</small>' : '<br><small>现场</small>') + '</td><td>' +
          escapeHtml(plan.packageName) + '</td><td>' + (current ? '<span class="step-cell"><b>' +
          escapeHtml(current.itemName) + '</b><small>' + escapeHtml(current.department) + ' · ' +
          escapeHtml(current.status) + '</small></span>' : '—') + '</td><td>' + Number(plan.completedSteps || 0) +
          ' / ' + Number(plan.totalSteps || 0) + '（' + Number(plan.progress || 0) + '%）</td><td><span class="status-pill' +
          statusClass + '">' + escapeHtml(plan.status) + '</span></td><td><div class="table-actions"><button data-plan-detail="' +
          escapeHtml(plan.planID) + '">查看</button></div></td></tr>';
      }).join("") + '</tbody></table>';
    target.querySelectorAll("[data-plan-detail]").forEach((button) => button.addEventListener("click", () => {
      openPlanDetail(plans.find((plan) => plan.planID === button.dataset.planDetail));
    }));
  }

  function openPlanDetail(plan) {
    if (!plan) return;
    const current = plan.currentStep;
    byId("dialogTitle").textContent = "体检计划详情";
    const planLines = [
      "计划 ID：" + plan.planID,
      "套餐：" + plan.packageName,
      "服务时间：" + formatTime(plan.serviceAt),
      "状态：" + plan.status,
      "完成进度：" + plan.completedSteps + " / " + plan.totalSteps
    ];
    const currentLines = current ? [
      current.itemName + " · " + current.department,
      "环节状态：" + current.status,
      "预计开始：" + formatTime(current.estimatedStart)
    ] : ["所有环节均已处理，当前没有待执行项目。"];
    dialogBody.innerHTML = '<div class="validation-panel metric-detail-panel">' +
      validationCard("患者", [plan.patient.name + " · " + plan.patient.phone], "ok") +
      validationCard("计划", planLines, "ok") + validationCard("当前环节", currentLines, current ? "warn" : "ok") +
      '</div><div class="dialog-actions"><button type="button" class="primary-button" id="cancelDialog">关闭</button></div>';
    dialog.showModal();
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
    if (event.target.id === "closeDialog") dialog.close();
    if (event.target.id === "cancelDialog") dialog.close();
  });

  byId("demoPatientTrigger").addEventListener("click", openDemoPatientTool);

  function demoToolTabs(active) {
    return '<div class="demo-tool-tabs"><button id="demoQueueTab" type="button" class="' +
      (active === 'queue' ? 'primary-button' : 'secondary-button') + '">排队模拟</button>' +
      '<button id="demoImportTab" type="button" class="' +
      (active === 'import' ? 'primary-button' : 'secondary-button') + '">患者资料导入</button>' +
      '<button id="demoRestrictionsTab" type="button" class="' +
      (active === 'restrictions' ? 'primary-button' : 'secondary-button') + '">解除限制</button></div>';
  }

  function bindDemoToolTabs() {
    byId("demoQueueTab").addEventListener("click", openDemoPatientTool);
    byId("demoImportTab").addEventListener("click", openDemoPatientImport);
    byId("demoRestrictionsTab").addEventListener("click", openDemoRestrictions);
  }

  async function openDemoRestrictions() {
    if (!state.me || !state.me.user.isOwner) return;
    byId("dialogTitle").textContent = "演示患者工具";
    dialogBody.innerHTML = demoToolTabs('restrictions') + '<div class="dialog-grid">' +
      '<div class="full demo-pool-status"><b id="demoRestrictionStatus">正在读取状态…</b>' +
      '<span id="demoRestrictionExpiry"></span></div>' +
      '<p class="full">临时解除本院项目的开放时间、科室关闭、项目停用、空腹与憋尿准备、前置及互斥关系，以及预约号源限制。排队和步行路线照常计算。</p>' +
      '<p class="full">开启后持续 2 小时，也可随时恢复。原有配置会保留，小程序重新进入项目选择或刷新路线后生效。</p>' +
      '<div class="dialog-actions"><button id="cancelDialog" type="button" class="secondary-button">关闭</button>' +
      '<button id="toggleDemoRestrictions" type="button" class="primary-button" disabled>正在读取…</button></div></div>';
    bindDemoToolTabs();
    const button = byId("toggleDemoRestrictions");
    const status = byId("demoRestrictionStatus");
    const expiry = byId("demoRestrictionExpiry");
    let enabled = false;
    let expiryTimer;
    function render(result) {
      clearTimeout(expiryTimer);
      state.demo = result;
      const restriction = result.restrictions || {};
      enabled = !!restriction.enabled && Date.parse(restriction.expiresAt) > Date.now();
      status.textContent = enabled ? '已临时解除项目限制' : '当前使用正常限制';
      expiry.textContent = enabled ? '自动恢复时间：' + new Date(restriction.expiresAt).toLocaleString('zh-CN') : '仅对当前医院生效';
      button.textContent = enabled ? '恢复正常限制' : '临时解除限制（2 小时）';
      button.disabled = false;
      if (enabled) expiryTimer = setTimeout(() => {
        if (dialog.open && byId("toggleDemoRestrictions") === button && !button.disabled) render(result);
      }, Math.max(1, Date.parse(restriction.expiresAt) - Date.now() + 100));
    }
    button.addEventListener("click", async () => {
      if (button.disabled) return;
      button.disabled = true;
      try {
        render(await api('/demo-patients/restrictions', { method: 'PUT', body: { enabled: !enabled } }));
        toast(enabled ? '本院项目限制已临时解除，2 小时后自动恢复' : '已恢复本院正常限制');
      } catch (error) { button.disabled = false; toast(error.message, "error"); }
    });
    if (!dialog.open) dialog.showModal();
    try { render(await api('/demo-patients')); }
    catch (error) { status.textContent = '状态读取失败，请重新打开此页'; toast(error.message, "error"); }
  }

  function openDemoPatientImport() {
    if (!state.me || !state.me.user.isOwner) return;
    byId("dialogTitle").textContent = "演示患者工具";
    let bundle = null;
    let fileVersion = 0;
    dialogBody.innerHTML = demoToolTabs('import') + '<form id="patientImportForm" class="dialog-grid">' +
      '<label>患者手机号<input name="phone" type="tel" pattern="1[0-9]{10}" maxlength="11" autocomplete="off" required /></label>' +
      '<label>登录密码<input name="password" type="password" minlength="8" maxlength="128" autocomplete="new-password" required /></label>' +
      '<label class="full">当前密码（已有账号且需要改密码时填写）<input name="currentPassword" type="password" maxlength="128" autocomplete="off" /><small>已有账号需验证当前密码；若不改密码，登录密码填原密码即可。</small></label>' +
      '<label class="upload-zone full">患者资料 JSON<input name="patientFile" type="file" accept=".json,application/json" required /><span>点击选择患者资料文件</span><small>JSON 只含资料、体检经历和报告，不含账号或密码。仅追加历史记录，重复导入不会新增相同体检。</small></label>' +
      '<div id="patientImportSummary" class="import-file-summary full">尚未选择文件</div>' +
      '<div class="dialog-actions"><button id="downloadPatientTemplate" type="button" class="secondary-button">下载本院示例</button>' +
      '<button id="cancelDialog" type="button" class="secondary-button">取消</button><button id="submitPatientImport" type="submit" class="primary-button">导入演示患者</button></div></form>';
    bindDemoToolTabs();
    const form = byId("patientImportForm");
    form.querySelector('[name="patientFile"]').addEventListener("change", async (event) => {
      const version = ++fileVersion;
      bundle = null;
      const file = event.target.files[0];
      const summary = byId("patientImportSummary");
      summary.textContent = "尚未选择文件";
      if (!file) return;
      try {
        if (file.size > 2 * 1024 * 1024) throw new Error("患者资料文件不能超过 2 MB");
        const parsed = JSON.parse((await file.text()).replace(/^\uFEFF/, ""));
        if (version !== fileVersion) return;
        if (!parsed || parsed.formatVersion !== 'patient-demo-1.0' || parsed.simulated !== true ||
            !parsed.patient || !Array.isArray(parsed.visits) || !parsed.visits.length) {
          throw new Error("请选择患者演示 JSON，可先下载本院示例");
        }
        const allowed = ['formatVersion', 'simulated', 'hospitalName', 'patient', 'visits'];
        if (Object.keys(parsed).some(key => !allowed.includes(key)) ||
            Object.keys(parsed.patient).some(key => !['name', 'gender', 'age', 'medicalHistory', 'allergens'].includes(key))) {
          throw new Error("JSON 含未知字段；手机号和密码请仅填写在页面中");
        }
        if (parsed.hospitalName !== state.me.hospital.hospitalName) throw new Error("数据包医院名称与当前医院不一致");
        const steps = parsed.visits.flatMap(visit => visit.steps || []);
        bundle = parsed;
        summary.textContent = parsed.patient.name + ' · ' + parsed.visits.length + ' 次体检 · ' + steps.length +
          ' 个项目 · ' + steps.filter(step => step.report).length + ' 份报告（模拟）';
      } catch (error) {
        if (version !== fileVersion) return;
        summary.textContent = error.message;
        toast(error.message, "error");
      }
    });
    byId("downloadPatientTemplate").addEventListener("click", async () => {
      try { downloadJson(await api('/demo-patients/import-template'), 'patient-demo-example.json'); }
      catch (error) { toast(error.message, "error"); }
    });
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const button = byId("submitPatientImport");
      if (button.disabled) return;
      if (!bundle) return toast("请先选择有效的患者资料 JSON", "error");
      const fields = formObject(form);
      button.disabled = true;
      try {
        const result = await api('/demo-patients/import', { method: 'POST', body: {
          phone: String(fields.phone || '').trim(), password: fields.password,
          currentPassword: fields.currentPassword || null, bundle: bundle
        }});
        form.reset();
        bundle = null;
        dialog.close();
        toast('已导入 ' + result.importedVisits + ' 次体检、' + result.importedReports + ' 份报告；跳过 ' + result.skippedVisits + ' 次重复记录');
      } catch (error) { toast(error.message, "error"); }
      finally { button.disabled = false; }
    });
    if (!dialog.open) dialog.showModal();
  }

  function openDemoPatientTool() {
    if (!state.me || !state.me.user.isOwner) return;
    const demo = state.demo || { prepared: 0, active: 0, inactive: 0 };
    byId("dialogTitle").textContent = "演示患者工具";
    dialogBody.innerHTML = demoToolTabs('queue') + '<form id="demoPatientForm" class="dialog-grid">' +
      '<div class="full demo-pool-status"><b>已预备 ' + demo.prepared + ' 人</b><span>当前纳入计算 ' +
      demo.active + ' 人 · 未激活 ' + demo.inactive + ' 人</span></div>' +
      '<label class="full">指定当前纳入人数<input name="count" type="number" min="1" max="100" value="' +
      (demo.active || 20) + '" required /><small>设置的是当前总人数；患者资料与项目组合均来自注册时固定的 100 人池。</small></label>' +
      '<div class="dialog-actions"><button type="button" class="danger-button" id="withdrawDemoPatients"' +
      (demo.active ? '' : ' disabled') + '>撤回全部</button><button type="button" class="secondary-button" id="cancelDialog">取消</button>' +
      '<button type="submit" class="primary-button">应用人数</button></div></form>';
    bindDemoToolTabs();
    byId("demoPatientForm").addEventListener("submit", async (event) => {
      event.preventDefault();
      const count = Number(new FormData(event.currentTarget).get("count"));
      try {
        const result = await api("/demo-patients/active", { method: "POST", body: { count: count } });
        dialog.close();
        await loadWorkspace();
        toast("已将 " + result.active + " 名固定演示患者纳入当前计算");
      } catch (error) { toast(error.message, "error"); }
    });
    byId("withdrawDemoPatients").addEventListener("click", async () => {
      if (!demo.active || !confirm("确定撤回全部演示患者吗？患者池会保留，可再次使用同一批患者。")) return;
      try {
        await api("/demo-patients/active", { method: "DELETE" });
        dialog.close();
        await loadWorkspace();
        toast("演示患者已全部撤回，固定患者池仍保留");
      } catch (error) { toast(error.message, "error"); }
    });
    if (!dialog.open) dialog.showModal();
  }

  function validateWorkspacePayload(payload) {
    const warnings = [];
    const errors = [];
    const sections = ["departments", "exams", "packages", "gis"];
    if (payload.formatVersion !== "1.0") warnings.push("formatVersion 建议使用 1.0。");
    sections.forEach((name) => {
      if (payload[name] != null && !Array.isArray(payload[name])) errors.push(name + " 必须是数组。");
    });
    const departmentKeys = new Set((payload.departments || []).map((item) => item.key));
    const examKeys = new Set((payload.exams || []).map((item) => item.key));
    (payload.exams || []).forEach((exam, index) => {
      if (!departmentKeys.has(exam.departmentKey)) {
        errors.push("exams[" + index + "] 引用了未声明科室 " + exam.departmentKey);
      }
      (exam.prerequisiteItemKeys || []).forEach((key) => {
        if (!examKeys.has(key)) errors.push("exams[" + index + "] 缺少前置项目 " + key);
      });
      (exam.conflictItemKeys || []).forEach((key) => {
        if (!examKeys.has(key)) errors.push("exams[" + index + "] 互斥项目不存在 " + key);
      });
    });
    (payload.packages || []).forEach((pkg, index) => {
      const included = new Set(pkg.includedItemKeys || []);
      (pkg.includedItemKeys || []).forEach((key) => {
        if (!examKeys.has(key)) errors.push("packages[" + index + "] 包含未声明项目 " + key);
      });
      (payload.exams || []).forEach((exam) => {
        if (!included.has(exam.key)) return;
        (exam.prerequisiteItemKeys || []).forEach((key) => {
          if (!included.has(key)) {
            errors.push("packages[" + index + "] 上架前缺少 " + exam.itemName + " 的前置项目 " + key);
          }
        });
        (exam.conflictItemKeys || []).forEach((key) => {
          if (included.has(key)) errors.push("packages[" + index + "] 存在互斥组合 " + exam.key + " / " + key);
        });
      });
    });
    return {
      errors: errors,
      warnings: warnings,
      affectedPackages: (payload.packages || []).map((item) => item.packageName || item.key).slice(0, 8)
    };
  }

  function renderImportPreview(payload) {
    if (!payload) {
      byId("importPreview").innerHTML = "";
      return;
    }
    const validation = validateWorkspacePayload(payload);
    const cards = [validationCard("导入预检", [
      "医院信息：" + (payload.hospital ? "1 项更新" : "不更新"),
      "科室：" + (payload.departments || []).length + " 项",
      "检查项目：" + (payload.exams || []).length + " 项",
      "套餐：" + (payload.packages || []).length + " 项",
      "GIS 楼层：" + (payload.gis || []).length + " 项"
    ], validation.errors.length ? "bad" : "ok")];
    if (validation.errors.length) cards.push(validationCard("需要修正", validation.errors.slice(0, 8), "bad"));
    if (validation.warnings.length) cards.push(validationCard("导入提醒", validation.warnings.slice(0, 8), "warn"));
    if (validation.affectedPackages.length) {
      cards.push(validationCard("涉及套餐", validation.affectedPackages, "warn"));
    }
    if (!validation.errors.length && !validation.warnings.length) {
      cards.push(validationCard("结构检查", ["未发现明显结构错误；提交时仍会执行后端事务校验。"], "ok"));
    }
    byId("importPreview").innerHTML = cards.join("");
  }

  function validateGisGeojson(geojson) {
    const departmentIds = new Set(state.departments.map((item) => item.deptID));
    const pointDeptIds = new Set();
    const routeDeptIds = new Set();
    let routeCount = 0;
    const warnings = [];
    const errors = [];
    if (!geojson || !Array.isArray(geojson.features)) {
      return { errors: ["GeoJSON 必须包含 features 数组。"], warnings: [], pointCount: 0, routeCount: 0 };
    }
    geojson.features.forEach((feature, index) => {
      const props = feature.properties || {};
      const geometry = feature.geometry || {};
      if (!["Point", "LineString", "Polygon", "MultiPolygon"].includes(geometry.type)) {
        errors.push("features[" + index + "] 使用了不支持的 geometry 类型。");
      }
      if (props.featureType === "department") {
        if (!props.deptID) errors.push("features[" + index + "] 科室点缺少 deptID。");
        else pointDeptIds.add(props.deptID);
      }
      if (props.featureType === "route") {
        routeCount += 1;
        if (props.fromDeptID) routeDeptIds.add(props.fromDeptID);
        if (props.toDeptID) routeDeptIds.add(props.toDeptID);
      }
    });
    const missingPoints = state.departments.filter((dept) => !pointDeptIds.has(dept.deptID))
      .map((dept) => dept.deptName).slice(0, 10);
    if (missingPoints.length) warnings.push("缺少科室点位：" + missingPoints.join("、"));
    routeDeptIds.forEach((id) => {
      if (!departmentIds.has(id)) errors.push("路线引用了不存在的科室 " + id);
    });
    if (pointDeptIds.size > 1 && !routeCount) {
      warnings.push("存在多个科室点，但没有 route 路线；导航可能只能显示目标点。");
    }
    if (state.gis.length > 1 && !routeCount) warnings.push("当前医院已有多个楼层，请确认换层路线是否完整。");
    return { errors: errors, warnings: warnings, pointCount: pointDeptIds.size, routeCount: routeCount };
  }

  function renderGisValidation(container, result) {
    const cards = [validationCard("GIS 预检", [
      "科室点位 " + result.pointCount + " 个",
      "科室路线 " + result.routeCount + " 条"
    ], result.errors.length ? "bad" : (result.warnings.length ? "warn" : "ok"))];
    if (result.errors.length) cards.push(validationCard("需要修正", result.errors.slice(0, 8), "bad"));
    if (result.warnings.length) cards.push(validationCard("导航提醒", result.warnings.slice(0, 8), "warn"));
    if (!result.errors.length && !result.warnings.length) {
      cards.push(validationCard("结构检查", ["未发现明显点位或路线问题。"], "ok"));
    }
    container.innerHTML = cards.join("");
  }

  byId("workspaceImportForm").querySelector('[name="workspaceFile"]').addEventListener("change", async (event) => {
    const input = event.currentTarget;
    const file = input.files[0];
    workspaceImportPayload = null;
    byId("importResult").classList.add("hidden");
    if (!file) {
      byId("importFileSummary").innerHTML = "<span>尚未选择文件</span>";
      renderImportPreview(null);
      return;
    }
    try {
      const text = await file.text();
      const payload = JSON.parse(text);
      const sections = ["departments", "exams", "packages", "gis"];
      sections.forEach((name) => {
        if (payload[name] != null && !Array.isArray(payload[name])) throw new Error(name + " 必须是数组");
      });
      workspaceImportPayload = payload;
      byId("importFileSummary").innerHTML = '<b>' + escapeHtml(file.name) + '</b><div class="import-counts">' +
        (payload.hospital ? '<span>医院信息 1</span>' : '') +
        '<span>科室 ' + (payload.departments || []).length + '</span><span>项目 ' + (payload.exams || []).length +
        '</span><span>套餐 ' + (payload.packages || []).length + '</span><span>GIS ' + (payload.gis || []).length +
        '</span></div><small>格式版本 ' + escapeHtml(payload.formatVersion || "未填写") + '</small>';
      renderImportPreview(payload);
    } catch (error) {
      input.value = "";
      byId("importFileSummary").innerHTML = "<span>文件解析失败，请重新选择</span>";
      renderImportPreview(null);
      const text = file ? await file.text() : "";
      toast(error instanceof SyntaxError ? syntaxLocationMessage(text, error) : error.message, "error");
    }
  });

  byId("workspaceImportForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    if (!workspaceImportPayload) return toast("请先选择有效的标准 JSON 文件", "error");
    const validation = validateWorkspacePayload(workspaceImportPayload);
    if (validation.errors.length) {
      renderImportPreview(workspaceImportPayload);
      return toast("导入预检未通过，请先修正文件", "error");
    }
    const submitButton = form.querySelector('[type="submit"]');
    submitButton.disabled = true;
    submitButton.textContent = "正在校验并导入…";
    try {
      const result = await api("/imports/workspace", { method: "POST", body: workspaceImportPayload });
      await loadWorkspace();
      const summary = result.summary;
      const summaryText = (name) => summary[name].created + " 新增 / " + summary[name].updated + " 更新";
      byId("importResult").innerHTML = '<b>导入成功</b><div class="import-result-grid">' +
        (summary.hospital.updated ? '<span>医院信息<small>已更新</small></span>' : '') + '<span>科室<small>' +
        summaryText("departments") + '</small></span><span>项目<small>' + summaryText("exams") +
        '</small></span><span>套餐<small>' + summaryText("packages") + '</small></span><span>GIS<small>' +
        summaryText("gis") + '</small></span></div>';
      byId("importResult").classList.remove("hidden");
      form.reset();
      workspaceImportPayload = null;
      byId("importFileSummary").innerHTML = "<span>导入完成，可继续选择其他文件</span>";
      renderImportPreview(null);
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
      downloadJson(template, "hospital-workspace-template.json");
      toast("标准模板已下载");
    } catch (error) { toast(error.message, "error"); }
  });

  byId("gisForm").querySelector('[name="gisFile"]').addEventListener("change", async (event) => {
    const input = event.currentTarget;
    const file = input.files[0];
    pendingGisUpload = null;
    byId("gisUploadPreview").innerHTML = "";
    if (!file) return;
    const text = await file.text();
    try {
      const geojson = JSON.parse(text);
      pendingGisUpload = geojson;
      const validation = validateGisGeojson(geojson);
      renderGisValidation(byId("gisUploadPreview"), validation);
      byId("gisPreviewMeta").textContent = "本地预览 · " + file.name;
      renderMap(byId("gisPreview"), geojson, state.dashboard ? state.dashboard.flow : []);
      renderGisValidation(byId("gisPreviewValidation"), validation);
    } catch (error) {
      input.value = "";
      byId("gisUploadPreview").innerHTML = "";
      toast(error instanceof SyntaxError ? syntaxLocationMessage(text, error) : error.message, "error");
    }
  });

  byId("gisForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    const file = data.get("gisFile");
    try {
      const geojson = pendingGisUpload || JSON.parse(await file.text());
      const validation = validateGisGeojson(geojson);
      if (validation.errors.length) {
        renderGisValidation(byId("gisUploadPreview"), validation);
        return toast("GIS 预检未通过，请先修正文件", "error");
      }
      await api("/gis/" + encodeURIComponent(data.get("floorKey")), { method: "PUT", body: { geojson: geojson } });
      form.reset();
      pendingGisUpload = null;
      byId("gisUploadPreview").innerHTML = "";
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
    if (!state.gis.length) {
      renderMapEmpty(byId("gisPreview"), "等待 GIS 文件", "上传 GeoJSON 后可在这里检查楼层轮廓和科室点位。");
      byId("gisPreviewValidation").innerHTML = "";
    }
  }

  async function previewGis(floor) {
    try {
      const data = await api("/gis/" + encodeURIComponent(floor));
      byId("gisPreviewMeta").textContent = floor + " · 版本 " + data.version + " · " + formatTime(data.updateTime);
      renderMap(byId("gisPreview"), data.geojson, state.dashboard ? state.dashboard.flow : []);
      renderGisValidation(byId("gisPreviewValidation"), validateGisGeojson(data.geojson));
    } catch (error) { toast(error.message, "error"); }
  }

  function renderAdjustmentOptions() {
    const deptOptions = state.departments.map((item) => '<option value="' + item.deptID + '">' + escapeHtml(item.deptName) + '</option>').join("");
    byId("anomalyDepartment").innerHTML = deptOptions || '<option value="">请先创建科室</option>';
  }

  byId("anomalyForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    try {
      await api("/anomalies", { method: "POST", body: formObject(form) });
      form.reset();
      await loadWorkspace();
      toast("现场异常已上报");
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
      const me = await api("/auth/me");
      authGeneration += 1;
      workspaceRequestId += 1;
      state.me = me;
      showApp();
      await loadWorkspace();
    } catch (_) {
      if (!state.me) showAuth();
    }
  }

  initialize();
})();
