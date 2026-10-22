# 切片 C：陨石组（Asteroid1 / Asteroid2 / Asteroid3）重构 Spec

## 边界

- 切片：`Asteroid1`、`Asteroid2`、`Asteroid3`（三份近乎相同的角色，合并为一个边界）
- 输入（触发）：
  - 绿旗 → 三个角色都隐藏
  - `broadcast scene_minigame` → 三个角色显示并从屏幕上方下落
  - 点击任意陨石 → 加分、播放 Pop；满 3 分进入结算
- 输出（可观察）：
  - 广播 `scene_ending`（第三分时只发一次）
  - 全局变量 `score`（0→1→2→3）、`playing`（1→0）
  - 三个角色的可见性、造型、线程在跑状态
  - 不新增/删除广播、全局变量、角色、造型、声音

## 重构目标

三份 `@respawn()` 里的五路 `if / else if / else` 分支，改成一次算术表达式；落点集合与概率完全一致：

```text
旧：random(1,5) == 1 → x=-140；==2 → -70；==3 → 0；==4 → 70；否则 140
新：set_x ((random(1, 5) - 3) * 70);
```

即 `@respawn()` 变成：

```text
def @respawn() {
    set_y 140;
    set_x ((random(1, 5) - 3) * 70);
}
```

其余脚本（下落循环、点击加分、结算、绿旗隐藏）保持不变。

## 必须保持不变

1. 三个触发帽：`scene_minigame`、`clicked`、`flag`。
2. `@respawn()` 的落点集合 `{-140, -70, 0, 70, 140}` 与等概率取值；`set_y 140` 不变。
3. 下落：每帧 `change_y -3`；低于 `-140` 时 respawn 并 `wait 0.4`；循环每轮 `wait 0.05`。
4. 点击：`if playing == 1` 才生效；`score += 1`；播放 `Pop`；
   `score >= 3` 时 `playing = 0`、`hide`、`broadcast scene_ending`，否则 `@respawn()`。
5. 三份文件的结构与行为保持一致（三个角色仍然各自独立，不改成克隆）。
6. 不改其他角色文件。

## 语法约束（fractch）

- 自定义块必须写成 `def @respawn() { ... }`（必须有 `def`）。
- 调用写成 `@respawn();`。
- 算术表达式直接写在参数里：`set_x ((random(1, 5) - 3) * 70);`

## 验收

1. 契约 C 与重构前零差异（`build/contracts_baseline/C.json`）。
2. 契约 A、B 也不受影响。
3. 扫描指标：该切片的重复度不高于重构前（0.235），依赖矩阵（发送/接收/写全局）不变。
4. 三份陨石的积木数应下降；旧实现保留为影子源码。
