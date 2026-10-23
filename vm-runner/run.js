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

// ---- 断言模式的轨迹采集（其他模式也会填，但不影响输出） ----
const FRAME_MS = 1000 / 30;      // 断言模式按 30fps 虚拟时钟步进
let currentFrame = -1;           // 当前帧号，事件用它定位
const broadcastLog = [];         // 每次 broadcast 发送（含无接收方）
const events = [];               // 台词 / 广播 / 运行时错误（原有语义）
const hatLog = [];               // 每个真正被启动的帽子线程
const cloneCreated = [];
const cloneRemoved = [];
const cloneAlive = new Set();
let clonePeak = 0;
let sayCount = 0;                // 台词也算「可观察变化」，否则只说话的角色会被误判成死角色

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

// 断言模式用的交互帽子：出现这些帽子，才要求「点了 / 按了必须有反应」
const INTERACTIVE_HATS = new Set([
  'event_whenthisspriteclicked',
  'event_whenstageclicked',
  'event_whenkeypressed',
]);

// 广播输入可能是 [1,[11,name,id]]（原始 JSON）或 {block: 菜单块id}（加载后）。
// 只取静态名字；用变量做广播名的情况这里取不到，交给运行时日志。
function broadcastNameFromInput(input, blocks) {
  if (!input) return '';
  const ref = Array.isArray(input) ? input[1] : input.block;
  if (typeof ref === 'string' && blocks[ref]) {
    const f = blocks[ref].fields && blocks[ref].fields.BROADCAST_OPTION;
    if (f && f.value) return String(f.value).toLowerCase();
  }
  if (Array.isArray(ref) && ref[0] === 11 && ref[1]) return String(ref[1]).toLowerCase();
  return '';
}

// 静态扫描：广播发送/接收名单 + 交互帽子分布。
// 运行时只能看到「执行到的」广播，静态扫描才能发现「发了没人收 / 收了没人发」。
function scanProject(rt) {
  const sent = new Set();
  const received = new Set();
  const hatsBySprite = {};
  let interactive = 0;
  for (const t of rt.targets) {
    if (!t.isOriginal) continue;
    const blocks = t.blocks && t.blocks._blocks ? t.blocks._blocks : {};
    const sprite = t.isStage ? '__stage__' : (t.getName?.() ?? t.id);
    for (const b of Object.values(blocks)) {
      if (!b || !b.opcode) continue;
      if (b.opcode === 'event_broadcast' || b.opcode === 'event_broadcastandwait') {
        const name = broadcastNameFromInput(b.inputs && b.inputs.BROADCAST_INPUT, blocks);
        if (name) sent.add(name);
      } else if (b.opcode === 'event_whenbroadcastreceived') {
        const f = b.fields && b.fields.BROADCAST_OPTION;
        if (f && f.value) received.add(String(f.value).toLowerCase());
      } else if (INTERACTIVE_HATS.has(b.opcode)) {
        interactive += 1;
        (hatsBySprite[sprite] = hatsBySprite[sprite] || []).push(b.opcode);
      }
    }
  }
  return {
    sent: [...sent].sort(),
    received: [...received].sort(),
    hatsBySprite,
    interactiveHats: interactive,
  };
}

// ---- 断言模式：固定帧距步进 + 确定性点击脚本 ----
// 与 playtest 的区别：不靠「每 500ms 轮询」，按 30fps 固定帧距推进，
// 帧号即证据（每个症状都能定位到第几帧）。
async function runAssertMode(vm, rt, seconds, forcedRoute, greenFlagScripts) {
  const frames = Math.max(60, Math.min(3600, Math.round(seconds * 30)));
  rt.currentStepTime = FRAME_MS;   // 必须设置：stepThreads 用 0.75*currentStepTime 作为每帧预算
  // 用真实时钟按 30fps 步进：wait / timer 走的是 Date.now，虚拟时钟会让
  // `forever` 循环的每帧工作预算永远耗不尽（timeElapsed 恒为 0）→ 死循环。
  await delay(FRAME_MS);

  {
    const stage = rt.getTargetForStage();
    const stateVars = () => {
      const out = {};
      for (const t of rt.targets) {
        if (!t.isOriginal) continue;
        const prefix = t.isStage ? '' : `${t.getName?.() ?? t.id}.`;
        for (const v of Object.values(t.variables || {})) {
          if (v.type === 'list' || v.type === 'broadcast_msg') continue;
          out[prefix + v.name] = v.value;
        }
      }
      return out;
    };
    const posesOf = () => rt.targets.filter((t) => t.isOriginal).map((t) => ({
      n: t.isStage ? '__stage__' : (t.getName?.() ?? t.id),
      x: Math.round((t.x || 0) * 10) / 10,
      y: Math.round((t.y || 0) * 10) / 10,
      visible: !!t.visible,
      size: t.size,
      costume: costumeName(t),
    }));
    const fingerprint = (vars) =>
      JSON.stringify([posesOf(), vars || stateVars(), cloneAlive.size, sayCount]);

    // ---- 状态驱动玩家（批次 10，方案 B）----
    // 旧「盲点轮询」会在同一角色的点击线程挂起期间（say X for N / wait）重复点击它，
    // 导致首个线程恢复后的后续广播丢失（02/07 误报根因）。新策略：
    // 一次只点一个「可见且带点击帽」的目标；点击后观察状态变化（或超时）再推进；
    // 同一目标自己的点击线程没跑完之前绝不重复点击（另加冷却兜底）。
    const OBSERVE_FRAMES = 30;    // 每次点击后最多观察 1s
    const COOLDOWN_FRAMES = 60;   // 同一目标两次点击至少间隔 2s
    const IDLE_SCAN_GAP = 15;     // 没有候选目标时 0.5s 后重新扫描
    const START_FRAME = 60;       // 绿旗 2s 后再开始交互

    // ---- 按键策略：分段扫屏（批次 12 修复）----
    // 旧「四键轮流各短按一次」对「方向键导航碰固定目标」类游戏净位移≈0：
    // 角色原地振荡，永远碰不到固定坐标的目标 → S1 误报（hetero_live_9jy3gl2y 实测）。
    // 新策略：按人工确认口径的段序（右25→左50→下25→上50→右25→左25→上25→下50，
    // 段间穿插空格脉冲），每段连发多次独立「按下 2 帧→松开」脉冲——离散脉冲同时
    // 兼容 when key 帽（按下沿触发一次）与 forever+if key pressed（按下期间逐步移动），
    // 不能长按住。
    // 参数与旧口径的两处偏差（人工验收语料倒逼，详见批次 12 报告）：
    //  1) 横向脉冲周期 36 帧（≈1.2s，按下 2 帧+松开 34 帧），不是 4 帧：
    //     sample_09「接蛋糕」类游戏的篮子按 sensing keypressed 状态每 0.05s 移动一步，
    //     蛋糕在下落（约 80 帧）期间要求篮子位移 <40px 才能接住（abs(x-Basket.x)<40 判定）；
    //     4 帧周期横穿速度 ≈90px/s 会让蛋糕全 miss → misses≥3 提前终局 → S1 误报。
    //     36 帧周期把横速压到 ≈12px/s，慢而持续，仍能撞墙完成横穿扫屏。
    //  2) 纵向/空格保持 4 帧快周期：接取类游戏不响应上下键，纵向快节奏补覆盖率。
    // 一轮 = 125×36 + 150×4 + 8×4 ≈ 5132 帧；断言窗（最长 60s≈1800 帧）跑不完
    // 就循环下一轮（导航类样本的目标都在首段横穿路径上，8s 窗内可通关）。
    const KEY_START = 90;         // 语义不变：绿旗 3s 后开始按键
    const H_PULSE = 36;           // 横向脉冲周期（按下 2 帧 + 松开 34 帧）
    const V_PULSE = 4;            // 纵向/空格脉冲周期（按下 2 帧 + 松开 2 帧）
    // 周期性空格长按（批次 12 修复，口径 B）：幕链里的 `until sensing.keypressed`
    // 轮询门（如 hetero_live_kgvzviv9 的 IntroOwl，循环体 ≈2s）只在每轮迭代采样
    // 一次条件，2 帧短脉冲几乎必然错过；且旧表里空格首脉冲在 ≈990 帧，660 帧断言
    // 窗内一次都没按过。改为每 300 帧（10s）长按 60 帧（2s）：采样间隔 ≤2s 的
    // 轮询门必中，when key "space" 帽也在按下沿触发。
    const SPACE_EVERY = 300;
    const SPACE_HOLD = 60;
    const SWEEP = (() => {
      const segs = [
        ['ArrowRight', 25, H_PULSE], [' ', 1, V_PULSE],
        ['ArrowLeft', 50, H_PULSE], [' ', 1, V_PULSE],
        ['ArrowDown', 25, V_PULSE], [' ', 1, V_PULSE],
        ['ArrowUp', 50, V_PULSE], [' ', 1, V_PULSE],
        ['ArrowRight', 25, H_PULSE], [' ', 1, V_PULSE],
        ['ArrowLeft', 25, H_PULSE], [' ', 1, V_PULSE],
        ['ArrowUp', 25, V_PULSE], [' ', 1, V_PULSE],
        ['ArrowDown', 50, V_PULSE], [' ', 1, V_PULSE],
      ];
      const events = new Map();   // local 帧 -> [{key, isDown}]（一帧可有多键事件）
      const push = (f, key, isDown) => {
        const arr = events.get(f) || [];
        arr.push({ key, isDown });
        events.set(f, arr);
      };
      let t = 0;
      for (const [key, n, period] of segs) {
        for (let i = 0; i < n; i += 1) {
          // 落在长按窗内的空格短脉冲跳过：其 +2 帧松开会把长按截断
          if (!(key === ' ' && (t % SPACE_EVERY) < SPACE_HOLD)) {
            push(t, key, true);
            push(t + 2, key, false);
          }
          t += period;
        }
      }
      for (let s = 0; s < t; s += SPACE_EVERY) {
        push(s, ' ', true);
        push(s + SPACE_HOLD, ' ', false);
      }
      return { cycle: t, events };
    })();

    // 哪些目标带点击帽（静态判定，克隆体不点，与旧版一致）
    const hasClickHat = new Map();
    for (const t of rt.targets) {
      if (!t.isOriginal) continue;
      const blocks = t.blocks && t.blocks._blocks ? t.blocks._blocks : {};
      const want = t.isStage ? 'event_whenstageclicked' : 'event_whenthisspriteclicked';
      hasClickHat.set(t.id, Object.values(blocks).some((b) => b && b.opcode === want));
    }
    const clickThreads = new Map();   // targetId -> Set<Thread>：点击帽启动的线程
    const lastClickFrame = new Map();
    let observe = null;               // { rec, before, deadline }
    let nextScanFrame = START_FRAME;

    const clickBusy = (t) => {
      const set = clickThreads.get(t.id);
      if (!set) return false;
      for (const th of set) {
        if (rt.threads.includes(th)) return true;   // 线程还在跑（含 PROMISE_WAIT）
      }
      set.clear();
      return false;
    };

    const actions = [];
    const series = {};
    let frozenActive = 0;
    let maxFrozenActive = 0;

    const click = (t, f) => {
      const rec = {
        kind: 'click',
        sprite: t.isStage ? '__stage__' : (t.getName?.() ?? t.id),
        frame: f,
        changed: false,
      };
      actions.push(rec);
      lastClickFrame.set(t.id, f);
      observe = { rec, before: fingerprint(), deadline: f + OBSERVE_FRAMES };
      try {
        vm.postIOData('mouse', {
          x: (t.x || 0) + 240, y: 180 - (t.y || 0), canvasWidth: 480, canvasHeight: 360,
        });
      } catch (e) { /* 鼠标位置只影响 mouse x/y，不影响点击帽 */ }
      const opcode = t.isStage ? 'event_whenstageclicked' : 'event_whenthisspriteclicked';
      const spawned = rt.startHats(opcode, null, t);
      if (Array.isArray(spawned) && spawned.length > 0) {
        const set = clickThreads.get(t.id) || new Set();
        for (const th of spawned) set.add(th);
        clickThreads.set(t.id, set);
      }
    };

    // 最近最少点击优先（LRU）：保证每个候选目标都会被轮到
    const pickTarget = (f) => {
      let best = null;
      let bestKey = Infinity;
      for (const t of rt.targets) {
        if (!t.isOriginal || !hasClickHat.get(t.id)) continue;
        if (!t.isStage && !t.visible) continue;
        const last = lastClickFrame.get(t.id);
        if (last !== undefined) {
          if (f - last < COOLDOWN_FRAMES) continue;
          if (clickBusy(t)) continue;
        }
        const key = last === undefined ? -1 : last;
        if (best === null || key < bestKey) { best = t; bestKey = key; }
      }
      return best;
    };

    let prevVars = stateVars();
    let lastFp = fingerprint(prevVars);

    for (let f = 0; f < frames; f += 1) {
      currentFrame = f;

      if (f === 0 && forcedRoute > 0 && stage) {
        const routeVar = stage.lookupVariableByNameAndType('route', '');
        if (routeVar) routeVar.value = forcedRoute;
      }
      if (f === 60 && forcedRoute > 0) {
        rt.startHats('event_whenbroadcastreceived',
          { BROADCAST_OPTION: 'start_tour' }, null);
      }
      // 分段扫屏：按 SWEEP 事件表轮转脉冲（一帧可有多键事件），跑完一轮回到 0 继续
      if (f >= KEY_START) {
        const local = (f - KEY_START) % SWEEP.cycle;
        const evs = SWEEP.events.get(local);
        if (evs) {
          for (const e of evs) {
            vm.postIOData('keyboard', { key: e.key, isDown: e.isDown });
          }
        }
      }
      // 状态驱动点击：没有进行中的观察、且到了扫描时点，就点一个 LRU 目标
      if (f >= START_FRAME && !observe && f >= nextScanFrame) {
        const t = pickTarget(f);
        if (t) click(t, f);
        else nextScanFrame = f + IDLE_SCAN_GAP;
      }

      rt._step();
      await delay(FRAME_MS);   // 让 wait / timer 按真实时间推进，同时固定 30fps 帧距

      const vars = stateVars();
      const fp = fingerprint(vars);
      for (const [k, v] of Object.entries(vars)) {
        if (prevVars[k] === v) continue;
        const n = Number(v);
        if (!Number.isNaN(n) && typeof v !== 'boolean') {
          (series[k] = series[k] || []).push([f, n]);
        }
      }
      prevVars = vars;

      if (fp !== lastFp) {
        frozenActive = 0;
        lastFp = fp;
      } else {
        const running = rt.threads.filter((t) => !t.isKilled).length;
        frozenActive = running > 0 ? frozenActive + 1 : 0;
        if (frozenActive > maxFrozenActive) maxFrozenActive = frozenActive;
      }

      // 观察窗结算：窗内任何一帧状态变化即算「点击有反应」
      if (observe) {
        if (fp !== observe.before) {
          observe.rec.changed = true;
          observe = null;
          nextScanFrame = f + 1;
        } else if (f >= observe.deadline) {
          observe = null;   // rec.changed 保持 false
          nextScanFrame = f + 1;
        }
      }
    }

    const targets = rt.targets.map((t) => ({
      name: t.getName?.() ?? t.id,
      isStage: !!t.isStage,
      isClone: !t.isOriginal,
      x: Math.round((t.x || 0) * 10) / 10,
      y: Math.round((t.y || 0) * 10) / 10,
      visible: !!t.visible,
      size: t.size,
      costume: costumeName(t),
      variables: Object.fromEntries(
        Object.values(t.variables || {})
          .filter((v) => v.type !== 'list' && v.type !== 'broadcast_msg')
          .map((v) => [v.name, v.value]),
      ),
    }));

    rt.stopAll();

    return {
      mode: 'assert',
      frames,
      frameMs: FRAME_MS,
      greenFlagScripts,
      scan: scanProject(rt),
      errors: events.filter((e) => String(e.type || '').includes('error')),
      broadcasts: broadcastLog,
      hats: hatLog,
      clones: {
        created: cloneCreated.length,
        removed: cloneRemoved.length,
        aliveAtEnd: cloneAlive.size,
        peak: clonePeak,
      },
      series,
      maxFrozenFrames: maxFrozenActive,
      actions,
      targets,
      threadsRunning: rt.threads.length,
      says: events.filter((e) => e.type === 'say' || e.type === 'think').length,
    };
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

  rt.on('SAY', (target, type, text) => {
    if (text) {
      sayCount += 1;
      events.push({ type, sprite: target.getName?.() ?? target.id, text: String(text) });
    }
  });
  rt.on('RUNTIME_ERROR', (msg) => events.push({ type: 'runtime_error', message: String(msg) }));
  rt.on('COMPILE_ERROR', (target, err) => events.push({ type: 'compile_error', message: String(err) }));
  // runtime 没有 broadcast 事件，hook startHats 捕获广播发送
  const startHats = rt.startHats.bind(rt);
  rt.startHats = (opcode, matchFields, target) => {
    const spawned = startHats(opcode, matchFields, target);
    const msg = matchFields && matchFields.BROADCAST_OPTION;
    // broadcast 的 startHats 调用发生在「发送」时，无论有没有接收帽都会进来，
    // 所以这里既能量到发送次数，也能靠 spawned 是否为空区分「有人接 / 没人接」。
    if (opcode === 'event_whenbroadcastreceived' && msg) {
      events.push({ type: 'broadcast', name: msg });
      broadcastLog.push({ name: String(msg).toLowerCase(), frame: currentFrame });
    }
    if (Array.isArray(spawned) && spawned.length > 0) {
      hatLog.push({
        opcode,
        name: msg ? String(msg).toLowerCase() : '',
        frame: currentFrame,
        sprite: target && target.getName ? target.getName() : '',
        threads: spawned.length,
      });
    }
    return spawned;
  };

  // 克隆体生命周期：只统计运行时新建的克隆（isOriginal === false）
  rt.on('targetWasCreated', (t) => {
    if (!t || t.isOriginal !== false) return;
    cloneAlive.add(t.id);
    if (cloneAlive.size > clonePeak) clonePeak = cloneAlive.size;
    cloneCreated.push({ sprite: t.getName ? t.getName() : '', frame: currentFrame });
  });
  rt.on('targetWasRemoved', (t) => {
    if (!t || !cloneAlive.has(t.id)) return;
    cloneAlive.delete(t.id);
    cloneRemoved.push({ sprite: t.getName ? t.getName() : '', frame: currentFrame });
  });

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

  // ---- 扩展 opcode stub（批次 12 修复，口径 A：环境仿真）----
  // 无头 VM 不加载任何扩展：music_* / pen_* 是未注册原语，sequencer 遇到未知
  // opcode 会静默终止当前线程且不发错误事件（hetero_live_kgvzviv9 实证：
  // IntroOwl 在 music_setInstrument 处线程死亡，scene_play 永不广播 → M7+S1
  // 误报；真实 Scratch 里这些积木正常工作，属于无头盲区而非候选缺陷）。
  // 处置：加载工程后把扩展命令块改写为等价的 control_wait（BEATS/TEMPO 等
  // 数值输入映射到 DURATION，保留原值子树，时长语义近似），幕链线程得以跑完。
  for (const t of rt.targets) {
    const blocks = t.blocks && t.blocks._blocks ? t.blocks._blocks : {};
    for (const [bid, b] of Object.entries(blocks)) {
      if (!b || !b.opcode || !/^(music|pen)_/.test(b.opcode)) continue;
      if (b.shadow) continue;
      // 扩展 reporter（提示词未教过，防御处理）：父块输入槽换成常量 0，删除本块
      const p = b.parent != null ? blocks[b.parent] : null;
      // 出现在父块「值输入槽」（排除 SUBSTACK 分支根，那是语句）= reporter 形态
      const slot = p ? Object.entries(p.inputs || {}).find(([k, inp]) =>
        k !== 'SUBSTACK' && k !== 'SUBSTACK2' &&
        (Array.isArray(inp) ? inp[1] === bid : inp && inp.block === bid)) : null;
      if (slot) {
        p.inputs[slot[0]] = [1, [4, '0']];
        delete blocks[bid];
        continue;
      }
      // 命令形态：改写为 control_wait
      let dur = null;
      for (const k of ['DURATION', 'BEATS', 'TEMPO', 'TIME']) {
        if (b.inputs && b.inputs[k]) { dur = b.inputs[k]; break; }
      }
      b.opcode = 'control_wait';
      b.inputs = { DURATION: dur || [1, [4, '0.25']] };
      b.fields = {};
    }
  }

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

  // 断言模式：不启动真实 frame loop，改为虚拟时钟逐帧步进（见 runAssertMode）
  if (mode === 'assert') {
    const result = await runAssertMode(vm, rt, seconds, forcedRoute, greenFlagScripts);
    console.log(JSON.stringify(result));
    if (typeof vm.quit === 'function') vm.quit();
    return;
  }

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
