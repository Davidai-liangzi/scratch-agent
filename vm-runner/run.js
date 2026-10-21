// 无头运行 Scratch 项目：加载 .sb3 → 绿旗 → 跑 N 秒 → 输出结构化状态快照 JSON
// 用法：node run.js <sb3路径> [秒数] [playtest]
//   playtest：交互冒烟之后再跑 N 秒并采样，用于「能不能玩到结束」的断言
// 输出：{ targets, threadsRunning, events, timeline } 写到 stdout，供调用方断言。
const fs = require('fs');
const VM = require('scratch-vm');
const { ScratchStorage } = require('scratch-storage');

const sb3Path = process.argv[2];
const seconds = Number(process.argv[3] || 5);
const mode = process.argv[4] || '';
const forcedRoute = Number(process.argv[5] || 0);

// scratch-vm 会往 stdout/stderr 打 "no renderer / no audio" 之类日志，吞掉以免污染输出
function muted(fn) {
  const out = process.stdout.write;
  const err = process.stderr.write;
  process.stdout.write = () => true;
  process.stderr.write = () => true;
  const restore = () => {
    process.stdout.write = out;
    process.stderr.write = err;
  };
  try {
    const r = fn();
    if (r && typeof r.then === 'function') return r.finally(restore);
    restore();
    return r;
  } catch (e) {
    restore();
    throw e;
  }
}

const toArrayBuffer = (b) =>
  b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength);
const delay = (ms) => new Promise((r) => setTimeout(r, ms));

// 当前造型名（舞台就是当前背景名）。采集它是为了判断「舞台有没有背景」「角色有没有造型」
// —— 素材解析失败时造型列表是空的，画面就是白屏，而语法/运行时都不报错。
function costumeName(t) {
  try {
    const list = t.getCostumes ? t.getCostumes() : [];
    const c = list[t.currentCostume];
    return c && c.name ? String(c.name) : '';
  } catch (e) {
    return '';
  }
}

async function main() {
  const vm = muted(() => {
    const v = new VM();
    v.attachStorage(new ScratchStorage());
    v.clear();
    return v;
  });
  const rt = vm.runtime;

  const events = [];
  rt.on('SAY', (target, type, text) => {
    if (text) events.push({ type, sprite: target.getName?.() ?? target.id, text: String(text) });
  });
  rt.on('RUNTIME_ERROR', (msg) => events.push({ type: 'runtime_error', message: String(msg) }));
  rt.on('COMPILE_ERROR', (target, err) => events.push({ type: 'compile_error', message: String(err) }));
  // runtime 没有 broadcast 事件，hook startHats 捕获广播发送
  const startHats = rt.startHats.bind(rt);
  rt.startHats = (opcode, matchFields, target) => {
    if (opcode === 'event_whenbroadcastreceived' && matchFields && matchFields.BROADCAST_OPTION) {
      events.push({ type: 'broadcast', name: matchFields.BROADCAST_OPTION });
    }
    return startHats(opcode, matchFields, target);
  };

  // 无头 Node 里没有 Web Audio：项目用到音乐扩展时，其构造函数会解码内置音效，
  // _decodeSound 因 runtime.audioEngine 为空返回 rejected promise，而 _loadAllSounds
  // 未 catch，Node 会当作「未处理拒绝」直接终止进程 → 验证被误报成崩溃。
  // 这类音频拒绝与项目正确性无关，静默兜住；其余拒绝仍记为事件，便于排查。
  process.on('unhandledRejection', (reason) => {
    const msg = reason && reason.message ? reason.message : String(reason);
    if (/No Audio Context Detected/.test(msg)) return;
    events.push({ type: 'runtime_error', message: msg });
  });

  const bytes = fs.readFileSync(sb3Path);
  await muted(() => vm.loadProject(toArrayBuffer(bytes)));

  // 统计绿旗脚本数量（供调用方判断「有绿旗脚本但没生效」这类问题）
  let greenFlagScripts = 0;
  for (const t of rt.targets) {
    const blocks = t.blocks && t.blocks._blocks ? t.blocks._blocks : {};
    for (const b of Object.values(blocks)) {
      if (b.opcode === 'event_whenflagclicked') greenFlagScripts++;
    }
  }

  // 起始快照：绿旗之前采集，用来判断「角色到底动没动」——
  // 只看最终坐标是否为 0,0 是不可靠的（初始就在 0,0 的角色会被误判成「没动」，
  // 初始不在 0,0 又没动的角色反而永远查不出来）。
  // 只对原始 target 采集（克隆体是运行时才出现的，没有初值）。
  const initialById = {};
  for (const t of rt.targets) {
    initialById[t.id] = {
      x: Math.round((t.x || 0) * 10) / 10,
      y: Math.round((t.y || 0) * 10) / 10,
      visible: !!t.visible,
      size: t.size,
      costume: costumeName(t),
    };
  }

  vm.greenFlag();
  rt.start(); // 关键：启动 frame loop（设置 currentStepTime，让线程真正推进）

  // 路线验收模式：提前把 route 设为指定值，选项的 route == 0 判定就不会误选；
  // 交互阶段结束后再由下面主动广播 start_tour，逐条验证三条路线。
  if (mode === 'routetest' && forcedRoute > 0) {
    await delay(800);
    const st = rt.getTargetForStage();
    const routeVar = st && st.lookupVariableByNameAndType('route', '');
    if (routeVar) routeVar.value = forcedRoute;
  }

  // 计时采样：供调用方判断「分数/时间/状态有没有真的变化」（试玩断言用）
  const timeline = [];
  const stageVars = () => {
    const st = rt.getTargetForStage();
    if (!st) return {};
    return Object.fromEntries(
      Object.values(st.variables || {})
        .filter((v) => v.type !== 'list' && v.type !== 'broadcast_msg')
        .map((v) => [v.name, v.value]),
    );
  };
  const sample = () => {
    if (timeline.length < 120) timeline.push({ t: timeline.length, vars: stageVars() });
  };
  const sampler = setInterval(sample, 1000);
  sample();

  await delay(seconds * 1000);
  clearInterval(sampler);
  sample();

  // 快照在交互冒烟**之前**采集，保持「绿旗空跑 N 秒」的原有语义
  // （否则点击引起的移动/显示变化会掩盖「绿旗脚本根本没生效」这类问题）。
  const targets = rt.targets.map((t) => ({
    name: t.getName?.() ?? t.id,
    isStage: !!t.isStage,
    isClone: !t.isOriginal,
    x: Math.round(t.x * 10) / 10,
    y: Math.round(t.y * 10) / 10,
    direction: t.direction,
    visible: t.visible,
    size: t.size,
    costume: costumeName(t),                 // 舞台这里就是「当前背景名」
    initial: initialById[t.id] || null,      // 绿旗前的起始快照；克隆体为 null
    variables: Object.fromEntries(
      Object.values(t.variables || {})
        .filter((v) => v.type !== 'list' && v.type !== 'broadcast_msg')
        .map((v) => [v.name, v.value]),
    ),
    lists: Object.fromEntries(
      Object.values(t.variables || {})
        .filter((v) => v.type === 'list')
        .map((v) => [v.name, v.value]),
    ),
  }));

  // ---- 交互冒烟：轮流点击每个可见角色、按常用键，看项目有没有任何反应 ----
  // 无头环境没有 renderer，mouse._pickTarget 永远返回舞台，所以不能靠 postIOData 的
  // isDown 触发点击帽；这里直接调 runtime.startHats（与 scratch-vm 的
  // _activateClickHats 走同一条路径）。按键走 postIOData('keyboard')，它会 emit
  // KEY_PRESSED，由 scratch3_event 负责启动 when key 帽。
  const fingerprint = () => JSON.stringify(rt.targets.map((t) => ({
    x: Math.round((t.x || 0) * 10) / 10,
    y: Math.round((t.y || 0) * 10) / 10,
    direction: Math.round((t.direction || 0) * 10) / 10,
    visible: !!t.visible,
    size: t.size,
    costume: costumeName(t),
    vars: Object.values(t.variables || {})
      .map((v) => [v.name, v.type, v.value])
      .sort(),
  })));

  const beforeFp = fingerprint();
  const eventMark = events.length;
  const clicked = [];
  const keys = [];

  // 隐藏的角色在 Scratch 里本来就点不到，跳过（否则会把「点不到」误判成「没反应」）
  const clickable = rt.targets.filter((t) => !t.isStage && t.isOriginal && t.visible);
  for (const t of clickable) {
    clicked.push(t.getName?.() ?? t.id);
    try {
      vm.postIOData('mouse', {
        x: t.x + 240, y: 180 - t.y, canvasWidth: 480, canvasHeight: 360,
      });
    } catch (e) { /* 鼠标位置只影响 mouse x/y，失败不影响点击本身 */ }
    rt.startHats('event_whenthisspriteclicked', null, t);
    await delay(600);
  }

  const stageTarget = rt.getTargetForStage();
  if (stageTarget) {
    clicked.push('__stage__');
    rt.startHats('event_whenstageclicked', null, stageTarget);
    await delay(400);
  }

  for (const key of ['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', ' ']) {
    keys.push(key === ' ' ? 'space' : key);
    vm.postIOData('keyboard', { key, isDown: true });
    await delay(350);
    vm.postIOData('keyboard', { key, isDown: false });
    await delay(120);
  }

  // 鼠标按住 0.4 秒：有些项目的开场用 `until sensing.mousedown()` 等玩家点击
  try {
    vm.postIOData('mouse', {
      isDown: true, x: 240, y: 180, canvasWidth: 480, canvasHeight: 360,
    });
    await delay(400);
    vm.postIOData('mouse', {
      isDown: false, x: 240, y: 180, canvasWidth: 480, canvasHeight: 360,
    });
    await delay(400);
  } catch (e) { /* 鼠标事件失败不影响其他断言 */ }

  // 二次点击：开场较长的项目，选项/目标可能在这时才显示出来。
  // 连点 3 次，覆盖「点击 3 次才算完成」的站点模板。
  const lateClickable = rt.targets.filter((t) => !t.isStage && t.isOriginal && t.visible);
  for (const t of lateClickable) {
    const name = t.getName?.() ?? t.id;
    if (!clicked.includes(name)) clicked.push(name);
    for (let i = 0; i < 3; i += 1) {
      try {
        vm.postIOData('mouse', {
          x: t.x + 240, y: 180 - t.y, canvasWidth: 480, canvasHeight: 360,
        });
      } catch (e) { /* 同上 */ }
      rt.startHats('event_whenthisspriteclicked', null, t);
      await delay(150);
    }
  }

  if (mode === 'routetest' && forcedRoute > 0) {
    rt.startHats('event_whenbroadcastreceived', { BROADCAST_OPTION: 'start_tour' }, null);
    await delay(600);
  }

  const stateChanged = fingerprint() !== beforeFp;
  const newSays = events.slice(eventMark).filter((e) => e.type === 'say' || e.type === 'think');
  const interaction = {
    clickable: clickable.length,       // 有多少个可见角色真的被点了
    clicked,
    keys,
    stateChanged,
    newSays: newSays.length,
    changed: stateChanged || newSays.length > 0,
  };

  // 试玩模式：交互之后再跑一段，并持续点击新出现的角色（路线站/选项），观察能否推进到结算
  if (mode === 'playtest' || mode === 'routetest') {
    const sampler2 = setInterval(sample, 1000);
    const endAt = Date.now() + seconds * 1000;
    while (Date.now() < endAt) {
      await delay(500);
      // 持续按空格：覆盖「按键 3 次」的站点模板
      vm.postIOData('keyboard', { key: ' ', isDown: true });
      await delay(60);
      vm.postIOData('keyboard', { key: ' ', isDown: false });
      const live = rt.targets.filter((t) => !t.isStage && t.isOriginal && t.visible);
      for (const t of live) {
        try {
          vm.postIOData('mouse', {
            x: t.x + 240, y: 180 - t.y, canvasWidth: 480, canvasHeight: 360,
          });
        } catch (e) { /* 点击失败不影响采样 */ }
        rt.startHats('event_whenthisspriteclicked', null, t);
      }
    }
    clearInterval(sampler2);
    sample();
  }

  rt.stopAll();

  // 事件去重 + 限长，避免循环里的 say 刷屏
  const seen = new Set();
  const dedup = [];
  for (const e of events) {
    const k = JSON.stringify(e);
    if (seen.has(k)) continue;
    seen.add(k);
    dedup.push(e);
    if (dedup.length >= 200) break;
  }

  console.log(JSON.stringify({
    targets,
    threadsRunning: rt.threads.length,
    greenFlagScripts,
    events: dedup,
    interaction,          // 交互冒烟结果：点了谁、按了哪些键、有没有任何反应
    timeline,             // 舞台变量随时间的变化（试玩断言用）
  }, null, 2));

  if (typeof vm.quit === 'function') vm.quit();
}

main().catch((e) => {
  console.error('VM_ERROR:', e && e.message ? e.message : e);
  process.exit(1);
});
