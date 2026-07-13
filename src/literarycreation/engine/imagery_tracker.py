"""意象追踪 — 跨章概念连续性分析（纯规则，零 LLM 调用）。"""
from __future__ import annotations

# 概念 → 义原词网
_IMAGERY_MAP: dict[str, list[str]] = {
    "水": ["雨","泪","江","河","海","潮","浪","洪水","冰","雪","雾","露",
           "淋湿","打湿","流","淌","溺","淹没","漂浮","涟漪","倒影","渡","桥","舟"],
    "火": ["烧","燃","焚","焰","灰烬","烟","灼","烤","烫","灯","烛","光",
           "热","暖","火山","熔","炭","火星","火烧","燎原","烧尽","燃烧"],
    "牢笼": ["锁","链","囚","笼","囚禁","困","束缚","捆绑","挣脱","逃脱",
             "高墙","铁窗","监视","监控","缠住","无法挣脱","深渊","泥潭"],
    "面具": ["笑","微笑","表演","戏","角色","面具","伪装","装作","强颜",
             "掩饰","隐藏","暗藏","假装","假","骗","不露声色","面不改色"],
    "路": ["路","道","街","巷","走廊","行","走","远行","追寻","方向",
           "路口","岔路","往回走","迷路","走到头","尽头","归途","旅程"],
    "伤口": ["伤","疤","愈合","血","痛","痊愈","缝","撕裂","崩溃",
             "站起来","重生","重来","重新","疗","养伤","敷药"],
    "光暗": ["光","亮","暗","黑","阴","影","灯塔","日光","月光","星光",
             "烛光","黎明","黄昏","夜幕","深渊","尽头","照耀","照亮"],
    "归属": ["家","故乡","故居","祠堂","祖宅","根","回到","归","离开",
             "告别","返","还","归去","故地","旧地","故居","老家","故土"],
    "风": ["风","吹","飘","卷","拂","掠过","气流","冷风","热风","风沙",
           "随风","逆风","风声","呼啸","狂风","微风"],
    "沉默": ["沉默","安静","无声","默","不出声","不响","哑","舌","无语",
             "说不出口","咽下去","噤","禁声","封口","压住"],
}


class ImageryTracker:
    """跨章意象轨迹扫描器。"""

    def __init__(self):
        self._trajectories: dict[str, list[tuple[int, list[str]]]] = {}

    def scan_chapter(self, text: str, chapter_idx: int) -> None:
        for concept, words in _IMAGERY_MAP.items():
            found = [w for w in words if w in text]
            if found:
                self._trajectories.setdefault(concept, []).append(
                    (chapter_idx, found[:8]))

    def analyze_trajectories(self) -> list[str]:
        warnings: list[str] = []
        for concept, trajectory in self._trajectories.items():
            if len(trajectory) < 2:
                continue
            chs = [t[0] for t in trajectory]
            for i in range(len(chs) - 1):
                gap = chs[i + 1] - chs[i]
                if gap > 3:
                    words_before = "、".join(trajectory[i][1][:3])
                    words_after = "、".join(trajectory[i + 1][1][:3])
                    warnings.append(
                        f"意象「{concept}」（如'{words_before}'）在第{chs[i]}章后消失 "
                        f"{gap} 章，于第{chs[i+1]}章（'{words_after}'）重新出现。"
                        f"间距过长可能导致意象再现突兀。")
                    break
        # 检测最活跃意象（建议作为全书核心意象）
        scored = sorted(
            [(c, sum(len(t[1]) for t in traj)) for c, traj in self._trajectories.items()],
            key=lambda x: -x[1])
        if scored:
            top = scored[0]
            if top[1] > 15:
                warnings.append(
                    f"核心意象「{top[0]}」在全书中出现频率最高（{top[1]}次含相关词汇）。"
                    f"建议在最后一章以新的变体重现这一意象，形成呼应。")
        return warnings
