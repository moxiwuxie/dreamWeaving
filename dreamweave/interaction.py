"""开放式交互：对 NPC 交谈/共舞/击打/观察，对场景物件查看/使用/采集。

交互结果只依赖世界配置、当前时间天气与交互次数，因此同一输入可复现；
NPC 的信任值由运行时维护，不写回世界包，避免试玩污染存档。
"""

from __future__ import annotations

import zlib
from typing import Any

NPC_ACTIONS = {"talk": "交谈", "dance": "共舞", "hit": "击打", "observe": "观察"}
PROP_ACTIONS = {"inspect": "查看", "use": "使用", "harvest": "采集"}

# 可扛在肩上的物件；采集这些物件时进入搬运状态。
CARRY_MESSAGES = {
    "crate": "你把木箱扛到肩上，比看上去沉得多。",
    "driftwood": "你把漂流木扛上肩，木头的咸味蹭在衣服上。",
    "net": "你把渔网卷起来扛在肩上，盐粒簌簌往下掉。",
}
NOTE_MAX_CHARS = 60

# 物件按场景类型摆放在中心广场附近，客户端据此生成对应几何体。
PROP_LAYOUTS: dict[str, list[dict[str, Any]]] = {
    "built": [
        {"kind": "well", "name": "石砌水井", "x": 7.0, "z": 7.0},
        {"kind": "bench", "name": "木长椅", "x": -9.0, "z": 5.0},
        {"kind": "stall", "name": "旧货摊", "x": -11.0, "z": -6.0},
        {"kind": "notice", "name": "告示板", "x": 4.0, "z": -11.0},
        {"kind": "crate", "name": "木箱", "x": 12.0, "z": -3.0},
    ],
    "coast": [
        {"kind": "boat", "name": "搁浅的小船", "x": 9.0, "z": 8.0},
        {"kind": "net", "name": "晾着的渔网", "x": -8.0, "z": -4.0},
        {"kind": "driftwood", "name": "漂流木", "x": -11.0, "z": 9.0},
        {"kind": "campfire", "name": "篝火堆", "x": 0.0, "z": -10.0},
    ],
    "natural": [
        {"kind": "tree", "name": "老树", "x": 10.0, "z": 9.0},
        {"kind": "rock", "name": "苔痕巨石", "x": -12.0, "z": 7.0},
        {"kind": "campfire", "name": "篝火堆", "x": 5.0, "z": -9.0},
        {"kind": "berry", "name": "浆果丛", "x": -8.0, "z": -10.0},
        {"kind": "log", "name": "倒木", "x": 13.0, "z": -5.0},
    ],
}

PROP_DESCRIPTIONS = {
    "well": "井壁是整块青石凿的，水面晃着一圈天光。",
    "bench": "长椅被坐得发亮，木纹里嵌着细沙。",
    "stall": "摊主不知去了哪里，货架上只剩几束麻绳。",
    "notice": "告示板上贴着粮税告示，边角被风掀起。",
    "crate": "木箱上了铜锁，箱盖上落了一层灰。",
    "boat": "船底卡在浅滩的沙里，缆绳还系在木桩上。",
    "net": "渔网挂满海盐，指尖一碰就簌簌落屑。",
    "driftwood": "木头被海水磨得发白，还带着咸味。",
    "campfire": "灰烬里留着暗红的火星，边上围着一圈石头。",
    "tree": "树皮裂开深纹，树冠把头顶的天遮去一半。",
    "rock": "岩石背面长满苔藓，摸上去又凉又湿。",
    "berry": "枝头挂着几串紫红浆果，酸味隔着皮都能闻到。",
    "log": "倒木横在草里，断口渗出松脂。",
}

WEATHER_LINES = {
    "rain": "这雨下得人心里发潮。",
    "snow": "雪压住了屋檐，走路当心。",
    "storm": "听这风声，今晚别出门。",
    "fog": "雾大，出城容易迷路。",
    "heatwave": "热得连狗都不叫了。",
    "coldwave": "这股寒气，柴火得多备些。",
    "sandstorm": "风里全是沙，说话都硌牙。",
}

TALK_GENERIC = (
    "今天风不大，适合赶路。",
    "你要是闲着，去广场那边看看，人多。",
    "最近粮价又动了，日子紧。",
    "我没什么好说的，各忙各的吧。",
    "你看着不像本地人，从哪条路来的？",
)


def _stable(*parts: Any) -> int:
    return zlib.crc32("|".join(str(part) for part in parts).encode("utf-8")) & 0x7FFFFFFF


def _pick(options: tuple[str, ...], seed: int) -> str:
    return options[seed % len(options)]


def scene_props(scene_id: str, category: str, bounds: dict[str, Any]) -> list[dict[str, Any]]:
    """生成场景内的可交互物件；位置为场景中心附近的相对偏移。"""
    if scene_id == "coast" or scene_id.endswith("_coast"):
        layout = PROP_LAYOUTS["coast"]
    elif category == "built":
        layout = PROP_LAYOUTS["built"]
    else:
        layout = PROP_LAYOUTS["natural"]
    low = bounds["min"]
    high = bounds["max"]
    cx = (low[0] + high[0]) / 2
    cz = (low[2] + high[2]) / 2
    fn = min(high[0] - low[0], high[2] - low[2]) * 0.05
    props: list[dict[str, Any]] = []
    for item in layout:
        scale = min(fn / 10.0, 1.6)
        props.append(
            {
                "id": f"{scene_id}:{item['kind']}",
                "scene_id": scene_id,
                "kind": item["kind"],
                "name": item["name"],
                "position_m": [
                    round(cx + item["x"] * scale, 2),
                    round(low[1], 2),
                    round(cz + item["z"] * scale, 2),
                ],
                "verbs": list(PROP_ACTIONS),
            }
        )
    return props


def npc_trust_label(value: float) -> str:
    if value >= 0.75:
        return "亲近"
    if value >= 0.55:
        return "友善"
    if value >= 0.35:
        return "平淡"
    return "戒备"


def talk_line(
    npc: dict[str, Any],
    *,
    activity: str | None,
    weather_kind: str | None,
    minute: int,
    turn: int,
) -> str:
    """按身份、日程、天气生成一句可复现的对白。"""
    name = str(npc.get("name") or npc.get("id"))
    persona = npc.get("persona") or {}
    goals = persona.get("goals") or []
    seed = _stable(npc.get("id"), minute // 20, turn)
    weather_line = WEATHER_LINES.get(str(weather_kind or ""), "")

    if activity == "sleep":
        return _pick(
            (
                "（翻了个身，含糊地嘟囔）……天还没亮呢。",
                "（被吵醒，眯着眼）有事明天说。",
            ),
            seed,
        )
    if activity == "eat":
        return _pick(
            (
                "先坐下吃点？锅里还有半碗。",
                "（嘴里塞着东西）你等下，我咽下去再说。",
            ),
            seed,
        )
    if activity == "patrol":
        return _pick(
            (
                "城门那边刚换岗，别在宵禁后乱走。",
                "最近夜里不太平，你最好早点回去。",
            ),
            seed,
        )
    if "木材" in "".join(str(goal) for goal in goals) or "富商" in "".join(str(goal) for goal in goals):
        return _pick(
            (
                "木材又涨了两成。你要是有货，我出好价。",
                "做生意讲究个时机，现在可不是好时候。",
                weather_line or "市场今天冷清得很。",
            ),
            seed,
        )
    if "秩序" in "".join(str(goal) for goal in goals):
        return _pick(
            ("城门无事，你自便。", "规矩就是规矩，别让我为难。"),
            seed,
        )
    if weather_line and seed % 3 == 0:
        return weather_line
    return _pick(TALK_GENERIC, seed)


def dance_line(npc: dict[str, Any], *, activity: str | None, turn: int) -> str:
    name = str(npc.get("name") or npc.get("id"))
    seed = _stable("dance", npc.get("id"), turn)
    if activity == "sleep":
        return f"{name}被吵醒，皱眉看着你，还是没起身。"
    return _pick(
        (
            f"{name}先是一怔，随后踩着你的拍子晃了两下。",
            f"{name}笑出声来，笨拙地跟着你转了个圈。",
            f"{name}摆摆手说不会，脚却先动了起来。",
        ),
        seed,
    )


def hit_line(npc: dict[str, Any], *, activity: str | None, turn: int) -> str:
    name = str(npc.get("name") or npc.get("id"))
    seed = _stable("hit", npc.get("id"), turn)
    if activity == "patrol":
        return f"拳头还没落下，{name}已把长矛横在身前：「再动手，我就按律拿人。」"
    return _pick(
        (
            f"你一拳打过去，{name}侧身避开，脸色沉了下来。",
            f"拳头落在{name}肩上，{name}踉跄半步，眼神变了。",
            f"{name}挨了一下，没有还手，只是退开两步盯着你。",
        ),
        seed,
    )


def observe_line(npc: dict[str, Any]) -> str:
    persona = npc.get("persona") or {}
    traits = persona.get("traits") or {}
    goals = "、".join(str(goal) for goal in persona.get("goals") or []) or "过好眼下的日子"
    style = persona.get("speech_style") or "话不多"
    extraversion = float(traits.get("extraversion", 0.5))
    mood = "神情松散" if extraversion < 0.4 else "目光一直在人群里打转"
    return f"{npc.get('name') or npc.get('id')}：{mood}，说话{style}。挂心的事是{goals}。"


def prop_response(
    prop: dict[str, Any],
    action: str,
    *,
    picked: bool,
    raining: bool,
    note_text: str | None = None,
    carried: str | None = None,
) -> dict[str, Any]:
    """返回 message/effects/picked/note/carry。effects 为玩家生理增量。"""
    kind = prop["kind"]
    name = prop["name"]
    effects: dict[str, float] = {}

    if action == "inspect":
        base = PROP_DESCRIPTIONS.get(kind, "看不出名堂。")
        note = str(note_text or "").strip()
        if kind == "notice" and note:
            base = f"{base} 角落还留着你的字：「{note}」"
        return {"message": f"{name}：{base}", "effects": effects}

    # 告示板：使用即书写，内容由玩家提供，最多 NOTE_MAX_CHARS 字。
    if kind == "notice":
        text = str(note_text or "").strip()[:NOTE_MAX_CHARS]
        if not text:
            return {"message": "你捏着炭笔在板面上比划，终究没有落笔。", "effects": effects}
        return {"message": f"你在告示板上写下：「{text}」", "effects": effects, "note": text}

    # 采集可搬运物件：扛到肩上，由运行时记录搬运状态。
    if action == "harvest" and kind in CARRY_MESSAGES:
        if carried:
            return {"message": f"肩上还扛着{carried}，先放下再搬。", "effects": effects}
        effects["fatigue"] = 0.06
        return {"message": CARRY_MESSAGES[kind], "effects": effects, "carry": True}

    if kind == "well":
        effects["thirst"] = 0.4
        return {"message": f"你摇起一桶{name}的水，凉意顺着喉咙下去。", "effects": effects}
    if kind == "bench":
        effects["fatigue"] = -0.3
        return {"message": "你在长椅上坐下，肩背松了下来。", "effects": effects}
    if kind == "stall":
        return {"message": "你翻了翻货架，只找到一截麻绳，便又放了回去。", "effects": effects}
    if kind == "crate":
        return {"message": "铜锁纹丝不动，箱子里像是装着铁器。", "effects": effects}
    if kind == "boat":
        effects["fatigue"] = 0.15
        return {"message": "你把船推离浅滩，划到湾口又靠了岸。", "effects": effects}
    if kind == "net":
        return {"message": "网里缠着几片海藻和一只空螺壳。", "effects": effects}
    if kind == "driftwood":
        effects["fatigue"] = 0.05
        return {"message": "你抱起一段漂流木，准备拖回营地。", "effects": effects}
    if kind == "campfire":
        effects["core_temperature_c"] = 0.35
        return {
            "message": "你在火边烤了烤手，火星噼啪炸开。" if not raining else "雨点落进火堆，白烟呛得你后退。",
            "effects": effects,
        }
    if kind == "tree":
        effects["fatigue"] = 0.05
        return {"message": "你折下一段枯枝，树冠里惊起一只鸟。", "effects": effects}
    if kind == "rock":
        return {"message": "石块太硬，只留下几道白痕。", "effects": effects}
    if kind == "berry":
        if picked:
            return {"message": "枝头只剩下青涩的小果，看来被人摘过了。", "effects": effects}
        effects["hunger"] = 0.18
        return {"message": "你摘了一把浆果，酸得眯起眼。", "effects": effects, "picked": True}
    if kind == "log":
        effects["fatigue"] = 0.04
        return {"message": "你从倒木上掰下一块松脂，手上全是香味。", "effects": effects}
    return {"message": f"你对{name}做了个动作，没有更多发现。", "effects": effects}


def apply_effects(simulation: Any, effects: dict[str, float]) -> dict[str, float]:
    """把交互产生的生理增量写回运行时，并返回实际变化。"""
    applied: dict[str, float] = {}
    player = simulation.player
    for key, delta in effects.items():
        if key == "hunger":
            before = player.hunger
            player.hunger = min(1.0, max(0.0, before + delta))
            applied[key] = round(player.hunger - before, 3)
        elif key == "thirst":
            before = player.thirst
            player.thirst = min(1.0, max(0.0, before + delta))
            applied[key] = round(player.thirst - before, 3)
        elif key == "fatigue":
            before = player.fatigue
            player.fatigue = min(1.0, max(0.0, before + delta))
            applied[key] = round(player.fatigue - before, 3)
        elif key == "core_temperature_c":
            before = player.core_temperature_c
            player.core_temperature_c = min(42.0, max(28.0, before + delta))
            applied[key] = round(player.core_temperature_c - before, 3)
    return applied
