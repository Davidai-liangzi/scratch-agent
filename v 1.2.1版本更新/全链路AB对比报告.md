# 全链路 A/B 对比报告（旧版 HEAD vs 当前版本）

生成时间：2026-10-20 17:48

## 实验设置

- 旧版：`git worktree` 从 HEAD（6fc822b，v1.2.0）检出；新版：当前工作树（Phase 1~6 改造后）。
- 每个 prompt 每版各生成 3 次，实际完成 30 次；同一 prompt 内只改随机种子/采样，不改需求。
- 关闭自动回炉（AUTO_FIX_ROUNDS=0）与自动打开 sb3，其余链路（大纲 → 素材 → 打包 → VM 运行验证）完全一致。
- 指标：素材集合 Jaccard（越低越不重样）、Vendi Score（越高越多样）、结构唯一率、台词 Distinct-2、VM 运行通过率、未知素材数、token 开销。

## 总体对比

| 指标 | 旧版 | 新版 |
| --- | --- | --- |
| 生成成功数 / 总次数 | 15 / 15 | 15 / 15 |
| VM 运行通过数 | 15 | 15 |
| VM 通过率 | 100% | 100% |
| 平均耗时（秒） | 101 | 121 |
| 平均 token | 31418 | 43322 |
| 平均角色数（造型去重） | 4.5 | 6.9 |
| 平均背景数 | 2.3 | 2.5 |
| 平均台词条数 | 12.6 | 13.9 |
| 素材 Jaccard（同 prompt 三次，越低越好） | 0.405 | 0.091 |
| Vendi Score（同 prompt 三次，越高越好） | 11.25 | 23.62 |
| 结构唯一率（越高越好） | 100% | 100% |
| 台词 Distinct-2（越高越好） | 0.593 | 0.405 |
| 未知素材占比（越低越好） | 9% | 5% |

## 分 prompt 明细

| prompt | 版本 | 成功 | VM | 角色数 | 背景数 | 台词数 | 结构唯一 | Jaccard | Vendi | Distinct-2 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 动物冒险 | 旧版 | 3/3 | 3 | 5.0 | 2.3 | 5.0 | 3/3 | 0.556 | 9.24 | 0.707 |
| 动物冒险 | 新版 | 3/3 | 3 | 5.3 | 1.0 | 8.0 | 3/3 | 0.182 | 12.57 | 0.568 |
| 购物中心游览 | 旧版 | 3/3 | 3 | 5.0 | 3.3 | 20.0 | 3/3 | 0.154 | 18.61 | 0.637 |
| 购物中心游览 | 新版 | 3/3 | 3 | 7.7 | 3.3 | 18.0 | 3/3 | 0.043 | 30.80 | 0.625 |
| 餐厅经营游戏 | 旧版 | 3/3 | 3 | 5.0 | 1.0 | 9.0 | 3/3 | 0.571 | 8.00 | 0.419 |
| 餐厅经营游戏 | 新版 | 3/3 | 3 | 5.0 | 2.0 | 14.0 | 3/3 | 0.000 | 19.59 | 0.609 |
| 太空射击游戏 | 旧版 | 3/3 | 3 | 3.0 | 1.0 | 3.3 | 3/3 | 0.600 | 3.84 | 0.856 |
| 太空射击游戏 | 新版 | 3/3 | 3 | 5.3 | 2.7 | 7.3 | 3/3 | 0.188 | 14.55 | 0.548 |
| 校园故事 | 旧版 | 3/3 | 3 | 4.3 | 4.0 | 25.7 | 3/3 | 0.143 | 16.54 | 0.718 |
| 校园故事 | 新版 | 3/3 | 3 | 11.0 | 3.7 | 22.0 | 3/3 | 0.043 | 40.60 | 0.612 |

## 每次运行选用的素材

### 动物冒险

- 旧版 #1（VM 通过）：bear、butterfly1、cat、dog1、duck、flowers、forest、woods
- 旧版 #2（VM 通过）：butterfly1、cat、chick、dog1、flowers、forest、guide、woods
- 旧版 #3（VM 通过）：bear、butterfly1、cat、chick、dog1、forest
- 新版 #1（VM 通过）：butterfly、cat、forest、hedgehog、squirrel、trees
- 新版 #2（VM 通过）：butterfly1、cat-a、cat-b、fox、grasshopper、woods_and_bench
- 新版 #3（VM 通过）：butterfly1、cat-a、cat-b、forest、heart、rabbit、squirrel

### 购物中心游览

- 旧版 #1（VM 通过）：abby、avery、balloon1、cake、guitar、indoor、mallatrium、restaurant、shop
- 旧版 #2（VM 通过）：abby、avery_walking、ben、cake、info_badge、nightcity
- 旧版 #3（VM 通过）：abby、atrium、bowtie、donut、foodcourt、indoor、shop、speaker、star、theater
- 新版 #1（VM 通过）：atrium、bus-a、bus-b、campus、glasses、guide、jaime-a、jaime-b、laptop、mall、shoes、visitor
- 新版 #2（VM 通过）：city、gift、heartface、kenney-guide、kenney-visitor、kenney_urban_mall、mall、plant、shelf
- 新版 #3（VM 通过）：atrium、avery_walking_a、avery_walking_b、avery_walking_c、avery_walking_d、gift_a、gift_b、kenney_customer、kenney_guide、mall_mall、restaurant、shop

### 餐厅经营游戏

- 旧版 #1（VM 通过）：abby、apple、cake、donut、room1
- 旧版 #2（VM 通过）：abby、ben、cake、donut、muffin、orange、party
- 旧版 #3（VM 通过）：abby、ben、cake、donut、orange、room1
- 新版 #1（VM 通过）：balloon、bench、field、food_truck_a、forest、harper_a、harper_b、harper_c、jungle、star
- 新版 #2（VM 通过）：kenney_chef、kenney_customer、kenney_urban_restaurant、taco、watermelon
- 新版 #3（VM 通过）：kenney-chef、kenney-customer、milk-a、muffin-a、orange2-a、spotlight

### 太空射击游戏

- 旧版 #1（VM 通过）：galaxy、planet2、rocketship、star
- 旧版 #2（VM 通过）：planet2、rocketship、star、stars
- 旧版 #3（VM 通过）：galaxy、planet2、rocketship、star
- 新版 #1（VM 通过）：line、nebula、robot、rocketship、rocks、space、star
- 新版 #2（VM 通过）：galaxy、gift、robot、rocketship-a、rocketship-b、rocketship-c、rocketship-d、rocks、space_city_1、space_city_2、star、stars
- 新版 #3（VM 通过）：galaxy、nebula、robot-a、rocketship、star

### 校园故事

- 旧版 #1（VM 通过）：abby、avery walking、bell、ben、chalkboard、hall、playing field、school
- 旧版 #2（VM 通过）：abby、balloon、ben、cake、casey、chalkboard、hall、party、playingfield
- 旧版 #3（VM 通过）：avery_walking、ben、blue_sky_2、cafeteria、casey、narrator、playing_field、school
- 新版 #1（VM 通过）：abby_a、abby_b、abby_c、abby_d、avery_walking_a、avery_walking_b、avery_walking_c、avery_walking_d、gift_a、gift_b、hall、party_hat_a、party_hat_b、party_hat_e、princess_a、princess_b、princess_c、princess_d、school、spotlight
- 新版 #2（VM 通过）：andie-a、balloon1-a、balloon1-b、balloon1-c、box、heart_purple、heart_red、jordyn-a、jordyn-b、jordyn-c、jordyn-d、mural、school、spotlight
- 新版 #3（VM 通过）：balloon、chalkboard、city、gift、guide、hero、kenney_mall、party_bg、spotlight、star

## 结论速览（按本次数据自动生成）

- 素材重复度：同 prompt 三次的 Jaccard 中位数 0.405（旧）→ 0.091（新），越低越不重样。
- 素材广度：Vendi 11.25（旧）→ 23.62（新）；平均角色造型数 4.5 → 6.9。
- 可运行性：15/15 与 15/15 全部通过打包 + VM 运行验证；未知素材占比 8.8% → 5.0%。
- 成本：平均 token 变为 1.38 倍、耗时 1.20 倍，来自更长的素材块（完整造型清单 + 外部素材 + 种子角色）。
- 台词 Distinct-2：0.593 → 0.405，同时平均台词数 12.6 → 13.9。
  台词量只多了一点，若降幅仍明显，说明新版措辞更集中（可能来自 storygen 模板句），属于后续可优化项。
- 尚未自动判定的是「素材贴切度」：新版选材更随机，理论上可能出现和主题关系弱的素材，需要按下面的盲评模板人工确认。

## 怎么读这份报告

- **Jaccard**：同一 prompt 三次生成的素材重合度。旧版按主题固定顺序取同一批素材，重合度明显更高；新版带 MMR 去重与最近使用轮换，越低说明越不重样。
- **Vendi**：这组素材整体有多「散」。素材完全相同为 1，两两都不相似约等于素材数。
- **结构唯一率**：脚本结构指纹去重后的比例，100% 表示没有一次撞结构。
- **未知素材占比**：工程里引用了 catalog 找不到的文件名（幻觉素材）的比例，越低越稳。
- **token / 耗时**：新版素材块更长，token 明显上升是预期内的多样性成本。

## 人工盲评模板（建议 5 分制）

把同一 prompt 的新旧产物混在一起、不标版本，按下面 5 项打分，各 1~5 分：

| 维度 | 说明 | 旧版均分 | 新版均分 |
| --- | --- | --- | --- |
| 素材贴切 | 角色/背景是否贴合主题，而不是随便凑 |  |  |
| 画面协调 | 同一工程内素材风格是否统一 |  |  |
| 编排完整 | 有没有开场、过程、结束，交互是否成立 |  |  |
| 内容趣味 | 台词/玩法是否有记忆点 |  |  |
| 重复感（反向） | 5 分表示完全看不出模板痕迹 |  |  |

> 盲评样本与原始 JSON 在 `%TEMP%\ab_results`，报告由 `tools/ab_run.py` 生成。
