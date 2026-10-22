# 切片 A：DriftRock 重构 Spec

## 边界

- 切片：`DriftRock`（单个角色）
- 输入（触发）：
  - 绿旗 → 隐藏
  - `broadcast scene_intro` → 显示并开始上下漂移
  - `broadcast rock_away` → 停止漂移并隐藏
- 输出（可观察）：
  - 可见性变化顺序：隐藏 → 显示 → 隐藏
  - 造型保持 `Rocks`
  - 本地变量 `rock_on`、`going_up`（名字、初值、取值时机不变）
  - 不发广播、不写全局变量
  - 漂移期间的线程在跑状态
- 外部耦合：无（不读其他角色、不读写舞台变量）

## 重构目标

只改内部结构，不改行为：

- 把 `when broadcast scene_intro` 里 `until rock_on == 0` 循环中的「漂移一步」
  （播放音符、等待、按 `going_up` 改变 y、到 ±50 翻转方向）抽成自定义块
  `@drift_step()`。
- 主循环只保留 `until rock_on == 0 { @drift_step(); }`。

## 必须保持不变

1. 触发帽与它们的顺序：`scene_intro`、`rock_away`、`flag`。
2. `rock_on` / `going_up` 的名字、初值与切换时机：
   - `scene_intro`：`going_up = 1`、`rock_on = 1`
   - `rock_away`：`rock_on = 0`
   - 到上界（> 50）时 `going_up = 0`，到下界（< -50）时 `going_up = 1`
3. 每步移动 8，边界 ±50；音乐/音效与等待节奏不变。
4. 可见性变化：结束漂移后隐藏。
5. 不新增/删除广播、全局变量、角色、造型、声音。

## 允许变化

- 在 `DriftRock/main.fractch` 内新增一个自定义块并调整循环结构。
- 不得修改其他角色的文件。

## 验收

1. 契约 A 与重构前零差异（`build/contracts_baseline/A.json`）。
2. 契约 B、C 也不受影响（其他文件未改）。
3. 扫描指标：DriftRock 的重复度不高于重构前（0.0），依赖矩阵不变。
4. 旧实现保留为影子源码，回归稳定后再删。
