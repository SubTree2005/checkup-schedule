"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

class FakeClassList {
  constructor(initial = []) { this.values = new Set(initial); }
  add(value) { this.values.add(value); }
  remove(value) { this.values.delete(value); }
  toggle(value, force) {
    const enabled = force === undefined ? !this.values.has(value) : Boolean(force);
    if (enabled) this.values.add(value); else this.values.delete(value);
    return enabled;
  }
  contains(value) { return this.values.has(value); }
}

class FakeElement {
  constructor(id) {
    this.id = id;
    this.value = "";
    this.textContent = "";
    this.innerHTML = "";
    this.className = "";
    this.dataset = {};
    this.disabled = false;
    this.files = [];
    this.listeners = {};
    this.classList = new FakeClassList();
    this.parentElement = { classList: new FakeClassList() };
    this.elements = new Proxy({}, {
      get: (target, key) => {
        if (!target[key]) target[key] = new FakeElement(String(key));
        return target[key];
      }
    });
    this.children = new Map();
  }
  addEventListener(type, listener) { this.listeners[type] = listener; }
  querySelector(selector) {
    if (!this.children.has(selector)) this.children.set(selector, new FakeElement(`${this.id}:${selector}`));
    return this.children.get(selector);
  }
  querySelectorAll() { return []; }
  removeAttribute(name) { delete this[name]; }
  showModal() { this.open = true; }
  close() { this.open = false; }
  contains() { return false; }
  click() {}
  reset() { this.resetCount = (this.resetCount || 0) + 1; }
  remove() {}
}

function response(payload, status = 200) {
  return {
    status,
    ok: status >= 200 && status < 300,
    json: async () => payload
  };
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

async function flush() {
  await Promise.resolve();
  await new Promise((resolve) => setImmediate(resolve));
  await Promise.resolve();
}

async function main() {
  const elements = new Map();
  const byId = (id) => {
    if (!elements.has(id)) elements.set(id, new FakeElement(id));
    return elements.get(id);
  };
  byId("authView").classList.add("hidden");
  byId("appView").classList.add("hidden");

  const me = {
    user: { name: "管理员", phone: "13800000000", isOwner: false },
    hospital: { hospitalName: "测试医院", isAvailable: true }
  };
  const dashboard = {
    generatedAt: "2026-09-04T00:00:00Z",
    metrics: {
      unresolvedAnomalies: 0,
      openDepartments: 0,
      departmentCount: 0,
      todayPlans: 0,
      inProgressPlans: 0,
      completedPlans: 0,
      averageWaitSeconds: 0
    },
    flow: [{ deptID: "dept-1", deptName: "内科", location: "一楼", peopleFlow: 8, estimatedWaitTime: 600 }]
  };
  const plans = {
    total: 1,
    items: [{
      planID: "plan-1",
      patient: { userID: "patient-1", name: "张三", phone: "13900000000" },
      packageName: "基础套餐",
      appointmentAt: "2026-09-04T01:30:00Z",
      serviceAt: "2026-09-04T01:30:00Z",
      status: "进行中",
      completedSteps: 1,
      totalSteps: 3,
      progress: 33,
      currentStep: { detailID: "detail-2", itemID: "exam-2", itemName: "腹部超声", department: "超声科", status: "进行中", estimatedStart: "2026-09-04T02:00:00Z" }
    }]
  };
  let fetchImpl = async (url) => {
    const payloads = {
      "/api/auth/me": me,
      "/api/departments": [],
      "/api/exams": [],
      "/api/packages": [],
      "/api/gis": [],
      "/api/plans?date=today&status=all&query=&limit=200": plans,
      "/api/dashboard/summary": dashboard,
      "/api/anomalies": []
    };
    assert.ok(Object.hasOwn(payloads, url), `unexpected initialization request: ${url}`);
    return response(payloads[url]);
  };

  const document = {
    getElementById: byId,
    querySelectorAll: () => [],
    querySelector: (selector) => byId(`selector:${selector}`),
    createElement: (tag) => new FakeElement(tag),
    body: { appendChild() {} }
  };
  const context = {
    console,
    document,
    fetch: (...args) => fetchImpl(...args),
    FormData: class {
      constructor(form) { this.form = form; }
      entries() { return Object.entries((this.form && this.form.formFields) || {}); }
      get(name) { return name === "floorKey" ? "1F" : null; }
    },
    FileReader: class {},
    Blob: class {},
    URL: { createObjectURL: () => "blob:test", revokeObjectURL() {} },
    setTimeout: () => 1,
    clearTimeout() {}
  };
  vm.createContext(context);
  const source = fs.readFileSync(path.join(__dirname, "../apps/admin-web/assets/app.js"), "utf8");
  assert.doesNotMatch(source, /\sstyle\s*=/i, "admin runtime must remain compatible with a CSP that blocks inline styles");
  vm.runInContext(source, context, { filename: "apps/admin-web/assets/app.js" });
  await flush();
  await flush();
  assert.equal(byId("appView").classList.contains("hidden"), false, "successful initialization should show the app");
  assert.match(byId("flowList").innerHTML, /<progress class="progress"/);
  assert.doesNotMatch(byId("flowList").innerHTML, /\sstyle\s*=/i);
  assert.match(byId("planTable").innerHTML, /张三/);
  assert.match(byId("planTable").innerHTML, /腹部超声/);
  assert.match(byId("planSummary").textContent, /共 1 人/);

  const workspaceFetch = fetchImpl;
  me.user.isOwner = true;
  await byId("demoPatientTrigger").listeners.click();
  assert.match(byId("dialogBody").innerHTML, /排队模拟/);
  assert.match(byId("dialogBody").innerHTML, /患者资料导入/);
  await byId("demoImportTab").listeners.click();
  assert.match(byId("dialogBody").innerHTML, /下载本院示例/);
  const patientForm = byId("patientImportForm");
  patientForm.formFields = { phone: '18888888888', password: 'test-demo-pass', currentPassword: '', patientFile: 'must-not-send' };
  const patientInput = patientForm.querySelector('[name="patientFile"]');
  const patientBundle = {
    formatVersion: 'patient-demo-1.0', simulated: true, hospitalName: me.hospital.hospitalName,
    patient: { name: '<林同学>', gender: '男', age: 21 },
    visits: [{ recordKey: 'visit-1', title: '模拟体检', steps: [{ itemName: '血常规', report: { conclusion: '模拟正常' } }] }]
  };
  patientInput.files = [{ size: 100, text: async () => JSON.stringify(patientBundle) }];
  await patientInput.listeners.change({ target: patientInput });
  assert.match(byId('patientImportSummary').textContent, /1 次体检/);
  let patientImportRequests = 0;
  fetchImpl = async (url, options) => {
    if (url === '/api/demo-patients/import') {
      patientImportRequests += 1;
      const body = JSON.parse(options.body);
      assert.equal(body.phone, '18888888888');
      assert.equal(body.password, 'test-demo-pass');
      assert.equal(body.currentPassword, null);
      assert.equal(body.bundle.patient.name, '<林同学>');
      assert.equal(body.bundle.phone, undefined);
      assert.equal(body.patientFile, undefined);
      return response({ importedVisits: 1, importedReports: 1, skippedVisits: 0 });
    }
    return workspaceFetch(url, options);
  };
  await patientForm.listeners.submit({ preventDefault() {}, currentTarget: patientForm });
  assert.equal(patientImportRequests, 1);
  assert.equal(patientForm.resetCount, 1);
  assert.equal(byId('editorDialog').open, false);
  await byId("demoImportTab").listeners.click();
  const invalidPatientInput = byId('patientImportForm').querySelector('[name="patientFile"]');
  invalidPatientInput.files = [{ size: 100, text: async () => JSON.stringify({ ...patientBundle, phone: '18888888888' }) }];
  await invalidPatientInput.listeners.change({ target: invalidPatientInput });
  await byId('patientImportForm').listeners.submit({ preventDefault() {} });
  assert.equal(patientImportRequests, 1, 'account fields in JSON must not be submitted');
  await byId('demoQueueTab').listeners.click();
  assert.match(byId('dialogBody').innerHTML, /指定当前纳入人数/);
  let restrictionRequests = 0;
  let restrictionState = { enabled: false, expiresAt: null };
  let restrictionPending;
  fetchImpl = async (url, options) => {
    if (url === '/api/demo-patients') return response({ restrictions: restrictionState });
    if (url === '/api/demo-patients/restrictions') {
      restrictionRequests += 1;
      assert.equal(options.method, 'PUT');
      const enabled = JSON.parse(options.body).enabled;
      restrictionState = { enabled, expiresAt: enabled ? new Date(Date.now() + 7200000).toISOString() : null };
      if (restrictionPending) await restrictionPending.promise;
      return response({ restrictions: restrictionState });
    }
    return workspaceFetch(url, options);
  };
  await byId('demoRestrictionsTab').listeners.click();
  assert.equal(byId('toggleDemoRestrictions').textContent, '临时解除限制（2 小时）');
  restrictionPending = deferred();
  const enabling = byId('toggleDemoRestrictions').listeners.click();
  await byId('toggleDemoRestrictions').listeners.click();
  assert.equal(restrictionRequests, 1, 'double clicking must not issue another toggle');
  assert.equal(byId('toggleDemoRestrictions').disabled, true);
  restrictionPending.resolve();
  await enabling;
  assert.equal(byId('toggleDemoRestrictions').textContent, '恢复正常限制');
  assert.match(byId('demoRestrictionExpiry').textContent, /自动恢复时间/);
  restrictionPending = null;
  await byId('toggleDemoRestrictions').listeners.click();
  assert.equal(restrictionRequests, 2);
  assert.equal(byId('demoRestrictionStatus').textContent, '当前使用正常限制');
  fetchImpl = async () => response({ detail: '更新失败' }, 500);
  await byId('toggleDemoRestrictions').listeners.click();
  assert.equal(byId('toggleDemoRestrictions').disabled, false, 'failed update must be retryable');
  assert.equal(byId('demoRestrictionStatus').textContent, '当前使用正常限制', 'failure must preserve the confirmed state');
  byId('editorDialog').close();
  me.user.isOwner = false;
  fetchImpl = workspaceFetch;
  let importCount = 0;
  fetchImpl = async (url, options) => {
    if (url === "/api/imports/workspace") {
      importCount += 1;
      return response({ summary: {
        hospital: { updated: 1 },
        ...Object.fromEntries(["departments", "exams", "packages", "gis"].map(name => [name, { created: 0, updated: 1 }]))
      } });
    }
    if (url === "/api/gis/1F" && options.method === "PUT") return response({});
    if (url === "/api/anomalies" && options.method === "POST") return response({});
    return workspaceFetch(url, options);
  };
  function dispatch(element, type) {
    const event = { currentTarget: element, preventDefault() {} };
    const pending = element.listeners[type](event);
    // Browsers clear currentTarget when synchronous event dispatch finishes.
    event.currentTarget = null;
    return pending;
  }
  const importForm = byId("workspaceImportForm");
  const importInput = importForm.querySelector('[name="workspaceFile"]');
  const bundleText = fs.readFileSync(path.join(__dirname, "../examples/hospitals/zijingang-campus-hospital/workspace.json"), "utf8");
  importInput.files = [{ name: "workspace.json", text: async () => bundleText }];
  await dispatch(importInput, "change");
  await dispatch(importForm, "submit");
  assert.equal(importCount, 1);
  assert.equal(importForm.resetCount, 1, "successful import resets the captured form after awaits");
  assert.match(byId("toast").textContent, /医院数据已完成一键导入/);
  assert.equal(importForm.querySelector('[type="submit"]').disabled, false);
  await dispatch(importForm, "submit");
  assert.equal(importCount, 1, "successful import clears the pending payload");

  for (const formId of ["workspaceImportForm", "registerForm"]) {
    const input = byId(formId).querySelector('[name="workspaceFile"]');
    input.value = "broken.json";
    input.files = [{ name: "broken.json", text: async () => "{" }];
    await dispatch(input, "change");
    assert.equal(input.value, "", "invalid file clears captured input after await");
  }
  const gisForm = byId("gisForm");
  const gisInput = gisForm.querySelector('[name="gisFile"]');
  gisInput.files = [{ name: "floor.json", text: async () => JSON.stringify({
    type: "FeatureCollection", features: [{ type: "Feature", properties: {}, geometry: { type: "Point", coordinates: [1, 1] } }]
  }) }];
  await dispatch(gisInput, "change");
  await dispatch(gisForm, "submit");
  assert.equal(gisForm.resetCount, 1);
  assert.match(byId("toast").textContent, /GIS 地图已发布新版本/);
  await dispatch(byId("anomalyForm"), "submit");
  assert.equal(byId("anomalyForm").resetCount, 1);
  assert.match(byId("toast").textContent, /现场异常已上报/);

  fetchImpl = async (url) => {
    assert.equal(url, "/api/auth/logout");
    throw new Error("network unavailable");
  };
  await byId("logoutButton").listeners.click();
  assert.equal(byId("appView").classList.contains("hidden"), false, "failed logout must keep authenticated view visible");
  assert.match(byId("toast").textContent, /退出失败/);

  const firstMap = deferred();
  const secondMap = deferred();
  fetchImpl = (url) => {
    if (url.endsWith("F1")) return firstMap.promise;
    if (url.endsWith("F2")) return secondMap.promise;
    throw new Error(`unexpected map request: ${url}`);
  };
  const floorSelect = byId("dashboardFloor");
  floorSelect.value = "F1";
  floorSelect.listeners.change({ target: floorSelect });
  floorSelect.value = "F2";
  floorSelect.listeners.change({ target: floorSelect });
  secondMap.resolve(response({
    geojson: { features: [{ geometry: { type: "Point", coordinates: [2, 2] }, properties: { name: "F2 marker" } }] },
    flow: []
  }));
  await flush();
  assert.match(byId("dashboardMap").innerHTML, /F2 marker/);
  firstMap.resolve(response({
    geojson: { features: [{ geometry: { type: "Point", coordinates: [1, 1] }, properties: { name: "stale F1 marker" } }] },
    flow: []
  }));
  await flush();
  assert.doesNotMatch(byId("dashboardMap").innerHTML, /stale F1 marker/, "stale map response must not overwrite the selected floor");

  const indoor = JSON.parse(fs.readFileSync(path.join(__dirname, "../gis/generated/workspace_gis_only.json"), "utf8"));
  const thirdFloor = indoor.gis.find((floor) => floor.floorKey === "3F").geojson;
  const mapFlow = [
    { deptID: "eye", deptName: "眼科（324）", location: "3F 324", peopleFlow: 15 },
    { deptID: "breath", deptName: "呼气试验室（306）", location: "3F 306（旁边为抽血处）", peopleFlow: 4 },
    { deptID: "zero", deptName: "骨密度（301）", location: "3F 301", peopleFlow: 0 },
    { deptID: "wrong-floor", deptName: "其他楼层", location: "2F 303", peopleFlow: 99 }
  ];
  async function showMap(geojson, flow) {
    fetchImpl = async () => response({ geojson, flow });
    floorSelect.value = "3F";
    floorSelect.listeners.change({ target: floorSelect });
    await flush();
    return byId("dashboardMap").innerHTML;
  }
  let mapHtml = await showMap(thirdFloor, mapFlow);
  assert.match(mapHtml, /眼科（324）：15 人/);
  assert.match(mapHtml, /呼气试验室（306）：4 人/);
  assert.match(mapHtml, /骨密度（301）：0 人/);
  assert.doesNotMatch(mapHtml, /99 人/);
  assert.match(mapHtml, /未关联科室人流/);
  assert.match(mapHtml, />15<\/text>/);
  mapHtml = await showMap(thirdFloor, [...mapFlow,
    { deptID: "duplicate", deptName: "同房间其他科室", location: "3F 324", peopleFlow: 8 }
  ]);
  assert.doesNotMatch(mapHtml, /眼科（324）：15 人/, "ambiguous rooms must not pick a department arbitrarily");
  const eyePoint = thirdFloor.features.find((feature) => feature.properties.room_ref === "324" && feature.geometry.type === "Point");
  mapHtml = await showMap({ features: [eyePoint, { ...eyePoint, id: "other-building" }] }, mapFlow);
  assert.doesNotMatch(mapHtml, /眼科（324）：15 人/, "duplicate room numbers across buildings must remain unbound");
  mapHtml = await showMap({ features: [{ ...eyePoint, properties: { ...eyePoint.properties, deptID: "breath" } }] }, mapFlow);
  assert.match(mapHtml, /呼气试验室（306）：4 人/, "explicit department IDs take precedence over room matching");
  mapHtml = await showMap({ features: [{ ...eyePoint, properties: { ...eyePoint.properties, deptID: "missing" } }] }, mapFlow);
  assert.doesNotMatch(mapHtml, /15 人/, "broken explicit IDs must not silently bind a different department");
  mapHtml = await showMap(indoor.gis.find((floor) => floor.floorKey === "1F").geojson, [
    { deptID: "lab", deptName: "检验科", location: "1F（血、尿、便检查）", peopleFlow: 13 }
  ]);
  assert.match(mapHtml, /检验科：13 人/, "named POIs must match an exact department name on the same floor");

  const oversizedCoordinates = Array.from({ length: 100001 }, (_, index) => [index, index]);
  fetchImpl = async () => response({
    geojson: { features: [{ geometry: { type: "LineString", coordinates: oversizedCoordinates }, properties: {} }] },
    flow: []
  });
  floorSelect.value = "oversized";
  floorSelect.listeners.change({ target: floorSelect });
  await flush();
  assert.match(byId("dashboardMap").innerHTML, /地图坐标过多/);

  console.log("admin web runtime checks passed: plan rendering, logout integrity, latest-floor rendering, GIS flow binding and coordinate limits");
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
