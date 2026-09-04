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
    FormData: class { entries() { return []; } },
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

  const oversizedCoordinates = Array.from({ length: 100001 }, (_, index) => [index, index]);
  fetchImpl = async () => response({
    geojson: { features: [{ geometry: { type: "LineString", coordinates: oversizedCoordinates }, properties: {} }] },
    flow: []
  });
  floorSelect.value = "oversized";
  floorSelect.listeners.change({ target: floorSelect });
  await flush();
  assert.match(byId("dashboardMap").innerHTML, /地图坐标过多/);

  console.log("admin web runtime checks passed: plan rendering, logout integrity, latest-floor rendering and GIS coordinate limits");
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
