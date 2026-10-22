# 切片 B：HangarShip + StationBot 重构 Spec

## 边界

- 切片：`HangarShip` 与 `StationBot`（因 StationBot 读取 `HangarShip.y`，两者合并）
- 输入（触发）：
  - 绿旗 → 两个角色都隐藏
  - `broadcast scene_station` → 飞船出现在机库中央并待机；机械师出现在右下角并提示
  - 点击 `HangarShip` → 解除待机，飞船点火起飞
- 输出（可观察）：
  - 广播 `scene_minigame`（起飞结束时，只发一次）
  - `HangarShip`：可见性、造型、本地变量 `ready`、线程在跑状态、台词
  - `StationBot`：可见性、造型、线程在跑状态、台词
  - `StationBot` 读取 `HangarShip.y > 60` 这一条件保持不变
- 不写全局变量，不新增/删除广播。

## 重构目标

只改内部结构，把两段长脚本各拆出可复用的自定义块，行为与事件顺序完全不变：

- `HangarShip/main.fractch`：
  - `@pulse_once()`：`change_effect brightness by 12; wait 0.2; change_effect brightness by -12; wait 0.2;`
  - `@fly_away()`：清效果 → 播放 Pop → 说「点火！抓稳了，我们冲进陨石最密的那片空域。」1 秒
    → 滑行 1 秒到 (0, 130) → `change_effect ghost by 60` → wait 0.4 → hide → `broadcast scene_minigame`
  - `scene_station` 主脚本只保留：显示/初始化 → `until ready == 0 { @pulse_once(); }` → `@fly_away();`
- `StationBot/main.fractch`：
  - `@wait_liftoff_and_leave()`：说「飞船出去了！三块陨石全点碎，航道就通了。」1.5 秒
    → 滑行 0.5 秒到 (200, -112) → hide
  - `scene_station` 主脚本只保留：显示/定位/第一句台词 → `until sprites["HangarShip"].y > 60 { 音符; wait 0.4; }` → `@wait_liftoff_and_leave();`

## 必须保持不变

1. 三个触发帽及其顺序：`scene_station`、`clicked`、`flag`（StationBot 没有 clicked）。
2. 所有台词文本、播放时长、音效/音符、滑行时间与目标坐标。
3. `HangarShip.ready` 的初值与切换时机：进入幕设 1，点击后设 0。
4. `broadcast scene_minigame` 只在起飞流程末尾发送一次。
5. `StationBot` 仍通过 `sprites["HangarShip"].y > 60` 判断起飞，不得改成其他机制。
6. 不新增/删除广播、全局变量、角色、造型、声音。

## 允许变化

- 仅在 `HangarShip/main.fractch` 与 `StationBot/main.fractch` 内新增自定义块并调整脚本结构。
- 不得修改其他角色的文件。

## 验收

1. 契约 B 与重构前零差异（`build/contracts_baseline/B.json`）。
2. 契约 A、C 也不受影响。
3. 扫描指标：该切片重复度不高于重构前（0.0），依赖矩阵（发送/接收/写全局/跨角色感知）不变。
4. 旧实现保留为影子源码，回归稳定后再删。
