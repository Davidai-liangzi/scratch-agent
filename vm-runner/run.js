// 无头运行 Scratch 项目：加载 .sb3 → 绿旗 → 跑 N 秒 → 输出结构化状态快照 JSON
// 用法：node run.js <sb3路径> [秒数]
// 输出：{ targets, threadsRunning, events } 写到 stdout，供调用方断言。
const fs = require('fs');
const VM = require('scratch-vm');
const { ScratchStorage } = require('scratch-storage');

const sb3Path = process.argv[2];
const seconds = Number(process.argv[3] || 5);

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

  vm.greenFlag();
  rt.start(); // 关键：启动 frame loop（设置 currentStepTime，让线程真正推进）

  await delay(seconds * 1000);

  rt.stopAll();

  const targets = rt.targets.map((t) => ({
    name: t.getName?.() ?? t.id,
    isStage: !!t.isStage,
    isClone: !t.isOriginal,
    x: Math.round(t.x * 10) / 10,
    y: Math.round(t.y * 10) / 10,
    direction: t.direction,
    visible: t.visible,
    size: t.size,
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
  }, null, 2));

  if (typeof vm.quit === 'function') vm.quit();
}

main().catch((e) => {
  console.error('VM_ERROR:', e && e.message ? e.message : e);
  process.exit(1);
});
