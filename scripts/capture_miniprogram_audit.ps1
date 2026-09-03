param(
  [string]$OutputDirectory = "artifacts\miniprogram-audit-current",
  [string]$Only = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repo = Split-Path -Parent $PSScriptRoot
$project = Join-Path $repo 'apps\miniprogram'
$ide = 'C:\Program Files (x86)\Tencent\微信web开发者工具\wechatide.cmd'
$output = Join-Path $repo $OutputDirectory
New-Item -ItemType Directory -Force -Path $output | Out-Null

function Invoke-Ide([string[]]$ToolArguments) {
  & $ide -c codex @ToolArguments | Out-Null
  if ($LASTEXITCODE -ne 0) { throw "微信开发者工具命令失败：$($ToolArguments[0])" }
}

function Invoke-Eval([string]$Body) {
  # wechatide.cmd forwards arguments through cmd.exe; keep the function on one line
  # so CR/LF characters cannot truncate a fixture silently.
  $compactBody = $Body -replace '[\r\n]+', ' '
  $source = "function(){ $compactBody }"
  Invoke-Ide @('automation_evaluate', '--project', $project, '--fn-source', $source)
}

function Ensure-Session {
  Invoke-Eval @'
wx.setStorageSync('patientToken','qa-token');
wx.setStorageSync('userInfo',{userID:'user-qa',name:'张三',gender:'男',age:31,phone:'13800005678'});
wx.setStorageSync('profile',{fasting:'no',bladder:'normal',drinkingWater:'adequate',medicalHistory:'无',allergens:'无'});
var app=getApp();
app.globalData.isLoggedIn=true;
app.globalData.userInfo={userID:'user-qa',name:'张三',gender:'男',age:31,phone:'13800005678'};
app.globalData.profile={fasting:'no',bladder:'normal',drinkingWater:'adequate',medicalHistory:'无',allergens:'无'};
app.globalData.selectedHospitalId=app.globalData.selectedHospitalId || 'hospital-qa';
app.globalData.selectedHospital=app.globalData.selectedHospital || {id:'hospital-qa',name:'浙江大学校医院（紫金港院区）'};
app.globalData.selectedCampusId=app.globalData.selectedCampusId || 'campus-qa';
app.globalData.selectedCampus=app.globalData.selectedCampus || {id:'campus-qa',name:'紫金港院区'};
return true;
'@
}

function Open-Page([string]$Url, [bool]$WaitForRequest = $false) {
  if ($Url -notmatch '^/pages/(login|register|legal)/') { Ensure-Session }
  Invoke-Ide @('automation_navigate', '--project', $project, '--action', 'reLaunch', '--url', $Url)
  if ($WaitForRequest) { Start-Sleep -Milliseconds 2800 } else { Start-Sleep -Milliseconds 350 }
}

function Save-Shot([string]$Name) {
  $path = Join-Path $output "$Name.png"
  Invoke-Ide @('simulator_screenshot', '--project', $project, '--path', $path)
  Write-Output "captured $Name"
}

function Capture([string]$Name, [string]$Url, [string]$AfterOpen = '', [bool]$WaitForRequest = $false) {
  if ($Only -and $Name -notmatch $Only) { return }
  Open-Page $Url $WaitForRequest
  if ($AfterOpen) { Invoke-Eval $AfterOpen }
  Start-Sleep -Milliseconds 180
  Save-Shot $Name
}

try {
  Invoke-Eval "getApp().clearLoginState(); return true;" | Out-Null
} catch {
  # The first connection may happen before the simulator runtime is ready.
}
Invoke-Ide @('simulator_refresh', '--project', $project) | Out-Null
Start-Sleep -Seconds 3
Invoke-Eval "getApp().clearLoginState(); return true;" | Out-Null
Capture '01-login' '/pages/login/login'
Capture '02-register' '/pages/register/register' "getCurrentPages().slice(-1)[0].setData({form:{name:'张三',gender:'男',age:'31',phone:'13800005678',password:'12345678',confirmPassword:'12345678',medicalHistory:'无',allergens:'无'},acceptedPolicies:true});"
Capture '03-terms' '/pages/legal/legal?type=terms'
Capture '04-privacy' '/pages/legal/legal?type=privacy'

$seedSession = @'
wx.setStorageSync('patientToken','qa-token');
wx.setStorageSync('userInfo',{userID:'user-qa',name:'张三',gender:'男',age:31,phone:'13800005678'});
wx.setStorageSync('profile',{fasting:'no',bladder:'normal',drinkingWater:'adequate',medicalHistory:'无',allergens:'无'});
var app=getApp();
app.globalData.isLoggedIn=true;
app.globalData.userInfo={userID:'user-qa',name:'张三',gender:'男',age:31,phone:'13800005678'};
app.globalData.profile={fasting:'no',bladder:'normal',drinkingWater:'adequate',medicalHistory:'无',allergens:'无'};
app.globalData.selectedHospitalId='hospital-qa';
app.globalData.selectedHospital={id:'hospital-qa',name:'浙江大学校医院（紫金港院区）'};
app.globalData.selectedCampusId='campus-qa';
app.globalData.selectedCampus={id:'campus-qa',name:'紫金港院区'};
return true;
'@
Invoke-Eval $seedSession

Capture '05-home' '/pages/index/index' "getCurrentPages().slice(-1)[0].syncPlan(null);" $true

$hospitalFixture = @'
getCurrentPages().slice(-1)[0].applyHospitals([
  {id:'hospital-qa',name:'浙江大学校医院',hospitalLevel:'一级甲等',positioning:'校内医疗服务',campuses:[
    {id:'campus-qa',hospitalID:'hospital-qa',name:'紫金港院区',available:true},
    {id:'campus-yq',hospitalID:'hospital-yq',name:'玉泉院区',available:true}
  ]},
  {id:'hospital-2',name:'浙江大学医学院附属第一医院',hospitalLevel:'三级甲等',positioning:'知名综合医院',campuses:[
    {id:'campus-2',hospitalID:'hospital-2',name:'之江院区',available:true}
  ]}
]);
'@
Capture '06-hospitals' '/pages/hospital/hospital' $hospitalFixture $true

Capture '08-mine' '/pages/mine/mine' "getCurrentPages().slice(-1)[0].renderUser({name:'张三',phone:'13800005678'});" $true
Capture '09-profile-edit' '/pages/edit-profile/edit-profile' "getCurrentPages().slice(-1)[0].applyProfile({name:'张三',gender:'男',age:31,phone:'13800005678'},{fasting:'no',bladder:'full',drinkingWater:'adequate',medicalHistory:'无',allergens:'无'});"
Capture '10-account-security' '/pages/account-security/account-security'
Capture '10b-delete-account' '/pages/delete-account/delete-account' "getCurrentPages().slice(-1)[0].setData({password:'12345678'});"
Capture '11-history-empty' '/pages/history/history' "getCurrentPages().slice(-1)[0].applyPlans([]);" $true
Capture '12-record-empty' '/pages/record/record' "getCurrentPages().slice(-1)[0].applyPlans([]);" $true

$catalogFixture = @'
var catalog={
  packages:[
    {id:'pkg-basic',name:'基础体检套餐',price:680,items:[
      {id:'blood',name:'血常规',department:'检验科',duration:15,fastingRequired:true},
      {id:'ecg',name:'心电图',department:'功能科',duration:15},
      {id:'us',name:'腹部超声（肝胆胰脾双肾）',department:'超声科',duration:25,fastingRequired:true},
      {id:'ct',name:'胸部CT',department:'放射科',duration:20},
      {id:'consult',name:'内科问诊',department:'内科',duration:15}
    ],notice:['请携带本人有效身份证件']},
    {id:'pkg-heart',name:'心脑血管专项体检套餐',price:1280,items:[
      {id:'pressure',name:'动态血压监测',department:'心血管内科',duration:30},
      {id:'carotid',name:'颈动脉超声',department:'超声科',duration:25},
      {id:'echo',name:'心脏彩超',department:'超声科',duration:30}
    ]},
    {id:'pkg-full',name:'全面健康评估套餐',price:1880,items:[
      {id:'oral',name:'口腔与颌面健康综合评估',department:'口腔科',duration:30},
      {id:'vision',name:'眼科常规检查',department:'眼科',duration:20}
    ]}
  ],
  departments:[
    {name:'检验科',projects:[{id:'blood',name:'血常规',duration:15,fastingRequired:true},{id:'urine',name:'尿常规',duration:10}]},
    {name:'超声科',projects:[{id:'us',name:'腹部超声（肝胆胰脾双肾）',duration:25,fastingRequired:true}]},
    {name:'功能科',projects:[{id:'ecg',name:'心电图',duration:15}]}
  ]
};
var app=getApp();
app.globalData.catalog=catalog;
app.globalData.currentPackageId='pkg-basic';
app.globalData.selectedItemIDs=[];
getCurrentPages().slice(-1)[0].applyCatalog(catalog);
'@
Capture '14-packages' '/pages/package/package' $catalogFixture $true

$packageDetailFixture = @'
var app=getApp();
var page=getCurrentPages().slice(-1)[0];
page.applySelection(app.globalData.catalog,'pkg-basic',[]);
'@
Capture '15-package-detail' '/pages/package-detail/package-detail' $packageDetailFixture $true
Capture '16-select-mode' '/pages/select-mode/select-mode'

$appointmentFixture = @'
var pad=function(value){return String(value).padStart(2,'0')};
var key=function(offset){var date=new Date();date.setHours(12,0,0,0);date.setDate(date.getDate()+offset);return date.getFullYear()+'-'+pad(date.getMonth()+1)+'-'+pad(date.getDate())};
var at=function(date,time){return date+'T'+time+':00+08:00'};
var d0=key(0),d1=key(1),d2=key(2),d3=key(3),d4=key(4);
getCurrentPages().slice(-1)[0].applyAvailability({dates:[
  {date:d0,available:true,slots:[{key:'0800',start:'08:00',end:'08:30',available:true,booked:6,appointmentAt:at(d0,'08:00')},{key:'0830',start:'08:30',end:'09:00',available:true,booked:12,appointmentAt:at(d0,'08:30')}]},
  {date:d1,available:true,slots:[{key:'0900',start:'09:00',end:'09:30',available:true,booked:8,appointmentAt:at(d1,'09:00')},{key:'0930',start:'09:30',end:'10:00',available:false,booked:20,appointmentAt:at(d1,'09:30')}]},
  {date:d2,available:true,slots:[{key:'1030',start:'10:30',end:'11:00',available:true,booked:5,appointmentAt:at(d2,'10:30')}]},
  {date:d3,available:false,slots:[]},
  {date:d4,available:true,slots:[{key:'1330',start:'13:30',end:'14:00',available:true,booked:4,appointmentAt:at(d4,'13:30')}]}
]});
'@
Capture '17-appointment-time' '/pages/appointment-time/appointment-time' $appointmentFixture $true

Invoke-Eval "var app=getApp(); app.globalData.appointmentDraft={appointmentAt:'2026-09-03T08:00:00+08:00',dateLabel:'09-03 周四',timeLabel:'08:00–08:30'}; app.globalData.selectedPlanMode='appointment'; return true;"
$reminderFixture = @'
getCurrentPages().slice(-1)[0].setData({wechatPushAvailable:true,wechatPush:true,subscriptionTemplateIds:['template-qa'],reminderStatusText:'体检前一天 20:00',systemCalendar:true});
'@
Capture '18-preparation-reminder' '/pages/preparation-reminder/preparation-reminder' $reminderFixture $true
Capture '19-preparation-confirm' '/pages/preparation-confirm/preparation-confirm'
Capture '20-preparation-arrangement' '/pages/preparation-arrangement/preparation-arrangement'

$planFixture = @'
var plan={
  id:'plan-qa',planID:'plan-qa',hospitalName:'浙江大学校医院（紫金港院区）',packageName:'基础体检套餐',
  date:'今天 08:30',appointmentAt:'2026-09-02T08:30:00+08:00',planStatus:'进行中',totalSteps:8,completedSteps:3,progress:38,remainingDuration:92,currentStepIndex:3,
  steps:[
    {detailID:'s1',title:'血常规',department:'检验科',status:'done',estimatedStart:'2026-09-02T08:00:00+08:00'},
    {detailID:'s2',title:'心电图',department:'功能科',status:'done',estimatedStart:'2026-09-02T08:20:00+08:00'},
    {detailID:'s3',title:'胸部CT',department:'放射科',status:'done',estimatedStart:'2026-09-02T08:40:00+08:00'},
    {detailID:'s4',title:'腹部超声（肝胆胰脾双肾）',department:'超声科（2层 A区）',location:'超声科（2层 A区）',status:'active',estimatedStart:'2026-09-02T09:10:00+08:00',queueWait:18,queueAhead:5,note:'憋尿'},
    {detailID:'s5',title:'口腔与颌面健康综合评估',department:'口腔科',status:'pending',estimatedStart:'2026-09-02T09:45:00+08:00'},
    {detailID:'s6',title:'内科问诊',department:'内科',status:'pending',estimatedStart:'2026-09-02T10:20:00+08:00'},
    {detailID:'s7',title:'尿常规',department:'检验科',status:'pending',estimatedStart:'2026-09-02T10:45:00+08:00'},
    {detailID:'s8',title:'视力与眼底综合检查',department:'眼科',status:'pending',estimatedStart:'2026-09-02T11:05:00+08:00'}
  ]
};
getApp().saveCurrentPlan(plan);
getCurrentPages().slice(-1)[0].syncPlan(plan);
'@
Capture '23-home-active' '/pages/index/index' $planFixture $true

$recordFixture = @'
getCurrentPages().slice(-1)[0].applyPlans([
  getApp().globalData.currentPlan,
  {id:'plan-today',planID:'plan-today',hospitalName:'浙江大学医学院附属第一医院（之江院区）',packageName:'心脑血管专项体检套餐',appointmentAt:'2026-09-02T14:30:00+08:00',planStatus:'待执行',totalSteps:6,completedSteps:0,steps:[]},
  {id:'plan-future',planID:'plan-future',hospitalName:'浙江大学校医院（玉泉院区）',packageName:'自选项目',appointmentAt:'2026-09-06T09:00:00+08:00',planStatus:'待执行',totalSteps:4,completedSteps:0,steps:[]}
]);
'@
Capture '24-record-multiple' '/pages/record/record' $recordFixture $true
Capture '25-live-plan' '/pages/plan/plan?planID=plan-qa' $planFixture $true

$overviewFixture = @'
getCurrentPages().slice(-1)[0].applyPlan(getApp().globalData.currentPlan);
'@
Capture '26-plan-overview' '/pages/plan-overview/plan-overview?planID=plan-qa' $overviewFixture $true

$navigationFixture = @'
getCurrentPages().slice(-1)[0].applyNavigation({
  fromName:'一层服务台',toName:'超声科（2层 A区）',distanceMeters:120,durationMinutes:3,location:'超声科（2层 A区）',floorInstruction:'直行 20 米后右转，乘电梯到 2 层。',
  map:{
    geojson:{features:[
      {type:'Feature',properties:{featureType:'buildingOutline'},geometry:{type:'Polygon',coordinates:[[[0,0],[100,0],[100,70],[0,70],[0,0]]]}},
      {type:'Feature',properties:{featureType:'room'},geometry:{type:'Polygon',coordinates:[[[8,8],[35,8],[35,28],[8,28],[8,8]]]}},
      {type:'Feature',properties:{featureType:'room'},geometry:{type:'Polygon',coordinates:[[[66,42],[94,42],[94,64],[66,64],[66,42]]]}}
    ]},
    routeCoordinates:[[18,18],[50,18],[50,52],[78,52]],
    fromPoint:{name:'服务台',coordinates:[18,18]},
    toPoint:{name:'超声科',coordinates:[78,52]}
  }
});
'@
Capture '27-navigation' '/pages/navigation/navigation?planID=plan-qa' $navigationFixture $true
Capture '28-plan-complete' '/pages/plan-complete/plan-complete?id=plan-qa'

$historyFixture = @'
getCurrentPages().slice(-1)[0].applyPlans([
  {id:'old-1',planID:'old-1',hospitalName:'浙江大学校医院（紫金港院区）',packageName:'基础体检套餐',completedAt:'2026-09-02T11:48:00+08:00',planStatus:'已完成',status:'已完成',finished:true,steps:[]},
  {id:'old-2',planID:'old-2',hospitalName:'浙江大学医学院附属第一医院（之江院区）',packageName:'自选项目',completedAt:'2026-05-18T10:20:00+08:00',planStatus:'已结束',status:'已结束',finished:true,steps:[]},
  {id:'old-3',planID:'old-3',hospitalName:'浙江大学校医院（玉泉院区）',packageName:'心脑血管专项体检套餐',completedAt:'2025-11-08T09:30:00+08:00',planStatus:'已完成',status:'已完成',finished:true,steps:[]}
]);
'@
Capture '29-history' '/pages/history/history' $historyFixture $true

$recordDetailFixture = @'
getCurrentPages().slice(-1)[0].applyRecord({
  id:'old-1',planID:'old-1',hospitalName:'浙江大学校医院（紫金港院区）',packageName:'基础体检套餐',completedAt:'2026-09-02T11:48:00+08:00',status:'已完成',planStatus:'已完成',
  steps:[
    {detailID:'s1',title:'血常规',status:'done'},
    {detailID:'s2',title:'腹部超声（肝胆胰脾双肾）',status:'done'},
    {detailID:'s3',title:'口腔与颌面健康综合评估',status:'done'},
    {detailID:'s4',title:'视力与眼底综合检查',status:'skipped'}
  ]
});
'@
Capture '30-record-detail' '/pages/record-detail/record-detail?id=old-1' $recordDetailFixture $true

$examDetailEmptyFixture = @'
var plan={id:'old-1',planID:'old-1',hospitalName:'浙江大学校医院（紫金港院区）',packageName:'基础体检套餐',steps:[{detailID:'s1',title:'腹部超声（肝胆胰脾双肾）',department:'超声科',duration:25,status:'done',navigationTarget:{locationText:'2层 A区'}}]};
getApp().globalData.viewingPlanRecord=plan;
var page=getCurrentPages().slice(-1)[0];
page.setData({planID:'old-1',detailID:'s1',reportFirst:false});
page.applyPlan(plan);
'@
Capture '31-exam-detail-empty-report' '/pages/exam-detail/exam-detail' $examDetailEmptyFixture $true

$examDetailReportFixture = @'
var plan={id:'old-1',planID:'old-1',hospitalName:'浙江大学校医院（紫金港院区）',packageName:'基础体检套餐',steps:[{detailID:'s1',title:'血常规',department:'检验科',duration:15,status:'done',reportStatus:'issued',navigationTarget:{locationText:'1层 检验科'},report:{conclusion:'本次检查结果总体正常。',reportedAt:'2026-09-02T11:55:00+08:00',items:[{id:'r1',label:'白细胞计数',value:'6.2',unit:'×10⁹/L',referenceRange:'3.5–9.5'},{id:'r2',label:'血红蛋白',value:'142',unit:'g/L',referenceRange:'130–175'}]}}]};
getApp().globalData.viewingPlanRecord=plan;
var page=getCurrentPages().slice(-1)[0];
page.setData({planID:'old-1',detailID:'s1',reportFirst:true});
page.applyPlan(plan);
'@
Capture '32-exam-detail-with-report' '/pages/exam-detail/exam-detail' $examDetailReportFixture $true

$homeReportFixture = @'
var plan={id:'report-1',planID:'report-1',hospitalName:'浙江大学校医院（紫金港院区）',packageName:'基础体检套餐',completedAt:'2026-09-02T11:48:00+08:00',planStatus:'已完成',finished:true,steps:[{detailID:'s1',title:'血常规',status:'done',reportStatus:'issued',report:{conclusion:'检查结果总体正常。',reportedAt:'2026-09-02T11:55:00+08:00',items:[{id:'r1',label:'白细胞计数',value:'6.2',unit:'×10⁹/L'}]}},{detailID:'s2',title:'心电图',status:'done'}]};
getCurrentPages().slice(-1)[0].syncHome([plan]);
'@
Capture '33-home-report' '/pages/index/index' $homeReportFixture $true

$recordHistoryFixture = @'
var page=getCurrentPages().slice(-1)[0];
page.setData({activeTab:'history'});
page.applyPlans([{id:'old-1',planID:'old-1',hospitalName:'浙江大学校医院（紫金港院区）',packageName:'基础体检套餐',completedAt:'2026-09-02T11:48:00+08:00',planStatus:'已完成',finished:true,steps:[{detailID:'s1',title:'血常规',status:'done',reportStatus:'issued',report:{conclusion:'检查结果总体正常。'}}]},{id:'old-2',planID:'old-2',hospitalName:'浙江大学医学院附属第一医院（之江院区）',packageName:'自选项目',completedAt:'2026-05-18T10:20:00+08:00',planStatus:'已结束',finished:true,steps:[]}]);
'@
Capture '34-record-history-tab' '/pages/record/record' $recordHistoryFixture $true

$aiChatFixture = @'
wx.removeStorageSync('aiAgentSession');
var page=getCurrentPages().slice(-1)[0];
page.syncPlan({id:'plan-ai',planID:'plan-ai',hospitalName:'浙江大学校医院（紫金港院区）',packageName:'基础体检套餐',planStatus:'进行中',totalSteps:4,completedSteps:1,progress:25,remainingDuration:68,currentStepIndex:1,steps:[{detailID:'ai-s1',title:'血常规',status:'done',estimatedStart:'2026-09-03T08:10:00+08:00'},{detailID:'ai-s2',title:'腹部超声',status:'active',department:'超声科',queueWait:18,estimatedStart:'2026-09-03T08:35:00+08:00'},{detailID:'ai-s3',title:'心电图',status:'pending',estimatedStart:'2026-09-03T09:10:00+08:00'},{detailID:'ai-s4',title:'内科问诊',status:'pending',estimatedStart:'2026-09-03T09:30:00+08:00'}]});
var component=page.selectComponent('#aiAgent');
component.openChat();
component.finishResponse('带我去体检总览','可以，从下面的卡片进入“查看体检总览”。',{kind:'navigation',actionID:'plan-overview',title:'查看体检总览',description:'查看本次体检各项目的完成情况与报告状态。',buttonText:'打开总览'});
'@
Capture '35-ai-chat' '/pages/index/index' $aiChatFixture $true

$aiThinkingFixture = @'
wx.removeStorageSync('aiAgentSession');
var page=getCurrentPages().slice(-1)[0];
page.syncPlan({id:'plan-ai',planID:'plan-ai',hospitalName:'浙江大学校医院（紫金港院区）',packageName:'基础体检套餐',planStatus:'进行中',totalSteps:4,completedSteps:1,progress:25,remainingDuration:68,currentStepIndex:1,steps:[{detailID:'ai-s1',title:'血常规',status:'done',estimatedStart:'2026-09-03T08:10:00+08:00'},{detailID:'ai-s2',title:'腹部超声',status:'active',department:'超声科',queueWait:18,estimatedStart:'2026-09-03T08:35:00+08:00'},{detailID:'ai-s3',title:'心电图',status:'pending',estimatedStart:'2026-09-03T09:10:00+08:00'},{detailID:'ai-s4',title:'内科问诊',status:'pending',estimatedStart:'2026-09-03T09:30:00+08:00'}]});
var component=page.selectComponent('#aiAgent');
component.openChat();
var userMessage={id:'qa-question',role:'user',content:'帮我查看最近一份体检报告',createdAt:Date.now()};
component.setData({messages:component.data.messages.concat(userMessage),thinking:true,scrollIntoView:'message-qa-question'});
'@
Capture '36-ai-chat-thinking' '/pages/index/index' $aiThinkingFixture $true

$aiSettingsFixture = @'
getCurrentPages().slice(-1)[0].setData({
  apiStatusText:'已接入',
  hasHistory:true,
  history:[
    {id:'chat-1',title:'解读血常规报告',preview:'白细胞计数在参考范围内代表什么？',timeText:'2026-09-03 19:20',active:true},
    {id:'chat-2',title:'腹部超声准备',preview:'检查前需要空腹多久？',timeText:'2026-09-02 20:15',active:false}
  ]
});
'@
Capture '37-ai-settings' '/pages/ai-settings/ai-settings' $aiSettingsFixture $true

$aiApiSettingsFixture = @'
getCurrentPages().slice(-1)[0].setData({modelName:'deepseek-v4-flash',configured:true,statusText:'已配置'});
'@
Capture '38-ai-api-settings' '/pages/ai-api-settings/ai-api-settings' $aiApiSettingsFixture $true

Write-Output "screenshots: $output"
