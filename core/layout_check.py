"""Phase C2：布局几何检查（不依赖渲染）。

用 vm-runner 导出的 targets（坐标/尺寸/可见性）做确定性检查：
- 可见角色是否整块跑到舞台外；
- 尺寸是否小到看不见或大到盖住舞台；
- 多个可点角色是否叠在同一点（点击会互相抢）。
"""

STAGE_X = 240
STAGE_Y = 180


def _visible_targets(validation) -> list:
    return [t for t in (validation or {}).get("targets") or []
            if not t.get("isStage") and t.get("visible")]


def check(validation) -> list:
    issues = []
    targets = _visible_targets(validation)
    for t in targets:
        name = str(t.get("name") or "?")
        x, y = float(t.get("x") or 0), float(t.get("y") or 0)
        size = float(t.get("size") or 100)
        if abs(x) > STAGE_X + 5 or abs(y) > STAGE_Y + 5:
            issues.append(f"{name} 在舞台外（{x:.0f},{y:.0f}），玩家看不到也点不到")
        if size <= 10:
            issues.append(f"{name} 的尺寸只有 {size:.0f}%，几乎看不见")
        elif size >= 400:
            issues.append(f"{name} 的尺寸 {size:.0f}% 过大，会盖住其他角色")

    for i in range(len(targets)):
        for j in range(i + 1, len(targets)):
            a, b = targets[i], targets[j]
            dx = abs(float(a.get("x") or 0) - float(b.get("x") or 0))
            dy = abs(float(a.get("y") or 0) - float(b.get("y") or 0))
            if dx < 12 and dy < 12 and float(a.get("size") or 100) > 40 \
                    and float(b.get("size") or 100) > 40:
                issues.append(f"{a.get('name')} 与 {b.get('name')} 叠在同一点"
                              f"（{a.get('x')},{a.get('y')}），点击会互相抢")
    return issues
