// 批次 4：按切片录制行为契约。
// 用法：node contract.js <sb3> <config.json>
// config: {frames, sampleEvery?, targets:[...], globals:[...], actions:[{frame,type,...}]}
// 只记录可观察状态（变量、可见性、造型、尺寸、是否仍有线程、广播、台词），
// 不记录坐标——坐标受真实时钟抖动影响，不适合做逐次比对的契约。
const fs = require('fs');
const VM = require('scratch-vm');
const { ScratchStorage } = require('scratch-storage');

const sb3Path = process.argv[2];
const config = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const frames = Number(config.frames || 180);
const FRAME_MS = 1000 / 30;

let currentFrame = -1;
const broadcastLog = [];
const sayLog = [];

function muted(fn) {
  const out = process.stdout.write;
  const err = process.stderr.write;
  process.stdout.write = () => true;
  process.stderr.write = () => true;
  const restore = () => { process.stdout.write = out; process.stderr.write = err; };
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

const toArrayBuffer = (b) => b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength);
const delay = (ms) => new Promise((r) => setTimeout(r, ms));

function costumeName(t) {
  try {
    const list = t.getCostumes ? t.getCostumes() : [];
    const c = list[t.currentCostume];
    return c && c.name ? String(c.name) : '';
  } catch (e) {
    return '';
  }
}

function varsOf(t) {
  const out = {};
  for (const v of Object.values(t.variables || {})) {
    if (v.type === 'list' || v.type === 'broadcast_msg') continue;
    out[v.name] = v.value;
  }
  return out;
}

async function main() {
  const vm = muted(() => {
    const v = new VM();
    v.attachStorage(new ScratchStorage());
    v.clear();
    return v;
  });
  const rt = vm.runtime;

  rt.on('SAY', (target, type, text) => {
    if (text) {
      const sprite = target.getName?.() ?? target.id;
      if ((config.targets || []).includes(sprite)) {
        sayLog.push({ sprite, text: String(text) });
      }
    }
  });
  const watchBroadcasts = new Set(config.broadcasts || []);
  const startHats = rt.startHats.bind(rt);
  rt.startHats = (opcode, matchFields, target) => {
    const spawned = startHats(opcode, matchFields, target);
    if (opcode === 'event_whenbroadcastreceived' && matchFields && matchFields.BROADCAST_OPTION) {
      const name = String(matchFields.BROADCAST_OPTION).toLowerCase();
      if (watchBroadcasts.has(name)) broadcastLog.push(name);
    }
    return spawned;
  };

  // 无头 Node 没有音频：音乐扩展解码内置音效的拒绝与切片行为无关，吞掉；
  // 其他未处理拒绝仍然报错，避免掩盖真实崩溃。必须在 loadProject 之前注册。
  process.on('unhandledRejection', (reason) => {
    const msg = reason && reason.message ? reason.message : String(reason);
    if (/No Audio Context Detected/.test(msg)) return;
    console.error('UNHANDLED_REJECTION:', msg);
  });

  const bytes = fs.readFileSync(sb3Path);
  await muted(() => vm.loadProject(toArrayBuffer(bytes)));

  const stage = rt.getTargetForStage();
  const actions = (config.actions || []).slice().sort((a, b) => a.frame - b.frame);
  let actionIndex = 0;
  const targetsWanted = config.targets || [];
  const globalsWanted = config.globals || [];

  const lastTarget = {};
  const lastGlobal = {};
  const result = {
    frames,
    action_frames: actions.map((a) => a.frame),
    targets: Object.fromEntries(targetsWanted.map((n) => [n, []])),
    globals: Object.fromEntries(globalsWanted.map((n) => [n, []])),
    broadcasts: [],
    says: [],
  };
  let lastBroadcastCount = 0;
  let lastSayCount = 0;

  function runAction(action) {
    if (action.type === 'broadcast') {
      rt.startHats('event_whenbroadcastreceived',
        { BROADCAST_OPTION: action.name }, null);
    } else if (action.type === 'setvar') {
      const variable = stage && stage.lookupVariableByNameAndType(action.name, '');
      if (variable) variable.value = action.value;
    } else if (action.type === 'click') {
      const target = rt.targets.find(
        (t) => t.isOriginal && t.getName && t.getName() === action.sprite);
      if (!target) return;
      try {
        vm.postIOData('mouse', {
          x: target.x + 240, y: 180 - target.y, canvasWidth: 480, canvasHeight: 360,
        });
      } catch (e) { /* 位置只影响 mouse x/y */ }
      rt.startHats('event_whenthisspriteclicked', null, target);
    } else if (action.type === 'key') {
      vm.postIOData('keyboard', { key: action.key, isDown: action.down !== false });
    }
  }

  function sample() {
    for (const name of targetsWanted) {
      const target = rt.targets.find(
        (t) => t.isOriginal && t.getName && t.getName() === name);
      if (!target) {
        const missing = { missing: true };
        if (JSON.stringify(missing) !== lastTarget[name]) {
          result.targets[name].push(missing);
          lastTarget[name] = JSON.stringify(missing);
        }
        continue;
      }
      const state = {
        visible: !!target.visible,
        costume: costumeName(target),
        size: Math.round((target.size || 0) * 100) / 100,
        running: rt.threads.some((th) => !th.isKilled && th.target === target),
        vars: varsOf(target),
      };
      const key = JSON.stringify(state);
      if (key !== lastTarget[name]) {
        result.targets[name].push(state);
        lastTarget[name] = key;
      }
    }
    if (stage) {
      for (const name of globalsWanted) {
        const variable = stage.lookupVariableByNameAndType(name, '');
        const value = variable ? variable.value : null;
        const key = JSON.stringify(value);
        if (key !== lastGlobal[name]) {
          result.globals[name].push(value);
          lastGlobal[name] = key;
        }
      }
    }
    if (broadcastLog.length !== lastBroadcastCount) {
      result.broadcasts = broadcastLog.slice();
      lastBroadcastCount = broadcastLog.length;
    }
    if (sayLog.length !== lastSayCount) {
      result.says = sayLog.map((s) => [s.sprite, s.text]);
      lastSayCount = sayLog.length;
    }
  }

  rt.currentStepTime = FRAME_MS;
  vm.greenFlag();
  await delay(FRAME_MS);

  for (let f = 0; f < frames; f += 1) {
    currentFrame = f;
    while (actionIndex < actions.length && actions[actionIndex].frame === f) {
      runAction(actions[actionIndex]);
      actionIndex += 1;
    }
    rt._step();
    await delay(FRAME_MS);
    sample();
  }

  rt.stopAll();
  console.log(JSON.stringify(result));
  if (typeof vm.quit === 'function') vm.quit();
}

main().catch((e) => {
  console.error('CONTRACT_ERROR:', e && e.message ? e.message : e);
  process.exit(1);
});
