"""HELL-1.2 rules. Pure state transitions; no I/O and no hidden global randomness."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json

VERSION = "HELL-1.2"
HOUSES = "ABCDEFG"
NPCS = "BCDEFG"
CAPS = {"gold": 30, "guards": 18, "influence": 20,
        "prestige": 16, "intel": 12, "scandal": 10, "crowns": 5}
ACTIONS = {"tax": 0, "reform": 2, "campaign": 3, "lobby": 3,
           "muster": 3, "spy": 1, "raid": 2, "sabotage": 0, "pass": 0}
MOTIONS = ("relief", "conscription", "academy", "embargo", "amnesty",
           "festival", "fortify", "common_tax", "reform_bill", "impeachment")
EVENTS = ("harvest", "drought", "plague", "omen", "trade",
          "spy_leak", "reform_wave", "border_incursion", "riot", "coup")
GOALS = ("kingmaker", "warlord", "tribune", "spymaster", "pacifier", "hoarder")
REPRESENTATIVES = {
    "A": "曜誓家·执政官", "B": "赤狮家·雷昂元帅",
    "C": "金帆家·玛薇会长", "D": "青藤家·塞莱娜议长",
    "E": "灰蛇家·维斯密探长", "F": "白塔家·阿德温学监",
    "G": "铁冠家·罗德里克公爵",
}
CHARACTER_LINES = {
    "A": "曜誓家的选择必须写进议会记录",
    "B": "赤狮看重兵力，也记得并肩的誓约",
    "C": "金帆会把每项条件列清，不做无账的交易",
    "D": "青藤愿意搭桥，但选票应有明确的去向",
    "E": "灰蛇分得清传言与已经签署的承诺",
    "F": "白塔要求证据，让今天的表态经得住明天追溯",
    "G": "铁冠重视信誉，联盟的期限不能含糊",
}

MOTION_TEXT = {
    "relief": "赈济：各家支付最多1黄金，帝国稳定+2",
    "conscription": "征兵：各家卫队+1，帝国稳定-1",
    "academy": "学院：各家情报+1、黄金-1，帝国稳定+1",
    "embargo": "禁运：最富有家族黄金-3，帝国稳定+1",
    "amnesty": "大赦：最高丑闻家族丑闻-2，帝国稳定+1",
    "festival": "庆典：各家黄金-1、威望+1，帝国稳定+1",
    "fortify": "边防：卫队最多家族卫队+2，帝国稳定-1",
    "common_tax": "共税：最富有家族黄金-2、最贫穷家族黄金+2",
    "reform_bill": "改革：各家黄金-2、威望+1，帝国稳定+1",
    "impeachment": "弹劾：最高丑闻家族威望-2、丑闻-1，帝国稳定+1",
}
EVENT_TEXT = {
    "harvest": "丰收：各家黄金+2；稳定+1",
    "drought": "旱灾：各家黄金-1；稳定-1",
    "plague": "瘟疫：各家卫队-1；稳定-2",
    "omen": "祥瑞：威望最高家族影响力+1；稳定+1",
    "trade": "远洋商队：最贫穷两家黄金各+2",
    "spy_leak": "情报泄漏：情报最多家族丑闻+1；稳定-1",
    "reform_wave": "改革风潮：威望最低两家威望+1；稳定+1",
    "border_incursion": "边境侵袭：卫队最多家族卫队-2；稳定-1",
    "riot": "暴动：最富有家族黄金-2；稳定-2",
    "coup": "未遂政变：随机家族卫队-1；稳定-2",
}


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def draw(seed, *parts):
    material = "|".join(map(str, (seed,) + parts))
    return int(hashlib.sha256(material.encode("utf-8")).hexdigest(), 16)


def pair(a, b):
    if a == b or a not in tuple(HOUSES) or b not in tuple(HOUSES):
        raise ValueError("Relations require two distinct houses")
    return ":".join(sorted((a, b)))


def relationship(state, a, b):
    return state["relations"][pair(a, b)]


def trust(state, a, b):
    return 0 if a == b else relationship(state, a, b)["trust"]


def pact(state, a, b):
    return any({p["a"], p["b"]} == {a, b} for p in state["pacts"])


def pact_count(state, a):
    return sum(a in (p["a"], p["b"]) for p in state["pacts"])


def change(state, house, field, amount):
    record = state["houses"][house]
    record[field] = max(0, min(CAPS[field], record[field] + amount))


def spend(state, house, field, amount):
    if amount < 0 or state["houses"][house][field] < amount:
        return False
    state["houses"][house][field] -= amount
    return True


def stability(state, delta):
    state["stability"] = max(0, min(12, state["stability"] + delta))


def adjust_relation(state, a, b, *, trust_delta=0, capital=0,
                    tension=0, refusal=0):
    rel = relationship(state, a, b)
    rel["trust"] = max(-5, min(5, rel["trust"] + trust_delta))
    rel["capital"] = max(0, min(10, rel["capital"] + capital))
    rel["tension"] = max(0, min(8, rel["tension"] + tension))
    rel["refusals"] = max(0, min(8, rel["refusals"] + refusal))
    return rel


def opening_state(seed, game_id="K7-NEW"):
    resources = {
        "A": (8, 5, 8, 6, 2), "B": (6, 7, 9, 6, 1),
        "C": (8, 7, 7, 7, 2), "D": (6, 5, 8, 8, 2),
        "E": (6, 5, 7, 6, 6), "F": (7, 6, 8, 8, 4),
        "G": (9, 6, 8, 7, 2),
    }
    houses = {
        h: dict(zip(("gold", "guards", "influence", "prestige", "intel"),
                    resources[h]), scandal=0, crowns=0)
        for h in HOUSES
    }
    relations = {}
    for ix, a in enumerate(HOUSES):
        for b in HOUSES[ix + 1:]:
            relations[pair(a, b)] = {
                "trust": draw(seed, "initial-trust", a, b) % 5 - 2,
                "capital": 0, "tension": 0, "refusals": 0,
            }
    pacts = [
        {"a": a, "b": b, "expires_after_round": 3}
        for a, b in (("A", "D"), ("B", "E"), ("C", "G"), ("D", "F"))
    ]
    for p in pacts:
        relationship({"relations": relations}, p["a"], p["b"])["trust"] = 3
    return {
        "version": VERSION, "game_id": game_id, "round": 0,
        "status": "ongoing", "stability": 12, "houses": houses,
        "relations": relations, "pacts": pacts, "contracts": [],
        "favor": {h: 0 for h in NPCS}, "last_outcome": None,
        "scores": {}, "winner": None,
    }


def secret_goals(seed):
    ordering = sorted(NPCS, key=lambda h: (draw(seed, "goal-assignment", h), h))
    return {**{"A": "player_mission"}, **dict(zip(ordering, GOALS))}


def select_max(state, field):
    return max(HOUSES, key=lambda h: (state["houses"][h][field], -ord(h)))


def select_min(state, field, n=1):
    return sorted(HOUSES, key=lambda h: (state["houses"][h][field], h))[:n]


def candidate_score(state, voter, candidate):
    person = state["houses"][candidate]
    if voter == candidate:
        return 5 + person["prestige"] // 3 - 2 * person["scandal"] - 2 * person["crowns"]
    rel = relationship(state, voter, candidate)
    history = 2 * rel["capital"] - 3 * rel["tension"]
    return (3 * rel["trust"] + (9 if pact(state, voter, candidate) else 0)
            + person["prestige"] // 3 - 2 * person["scandal"]
            - 2 * person["crowns"] + history)


def favorite(state, voter):
    return max(HOUSES, key=lambda c: (candidate_score(state, voter, c), -ord(c)))


def backing_threshold(state, voter, candidate):
    best = max(candidate_score(state, voter, c) for c in HOUSES)
    rel = (relationship(state, voter, candidate) if voter != candidate
           else {"capital": 0, "tension": 0})
    gap = max(0, best - candidate_score(state, voter, candidate))
    return max(1, gap + 4 - min(5, rel["capital"] // 2) + min(4, rel["tension"]))


def promised(state, voter, round_no):
    for contract in state["contracts"]:
        if contract["voter"] == voter and contract["election_round"] == round_no:
            partner = contract.get("partner", contract["candidate"])
            if pact(state, voter, partner):
                return contract["candidate"]
    return None


def next_election(round_no):
    return next((r for r in (4, 8, 12, 16, 20) if r >= round_no), None)


def npc_plan(state, seed, r, npc):
    h = state["houses"][npc]
    roll = draw(seed, r, npc, "main") % 10
    action = "tax" if h["gold"] <= 3 else (
        ("tax", "tax", "reform", "campaign", "muster", "lobby",
         "spy", "tax", "muster", "raid")[roll])
    opponents = [x for x in HOUSES if x != npc]
    opponents.sort(key=lambda t: (draw(seed, r, npc, "opponent", t), t))
    allies = [h for h in HOUSES if h != npc and pact(state, npc, h)]
    allies.sort(key=lambda x: (draw(seed, r, npc, "ally", x), x))
    targets = [h for h in NPCS if h != npc]
    targets.sort(key=lambda t: (-trust(state, npc, t),
                                draw(seed, r, npc, "diplomacy", t), t))
    court = {"type": "none"}
    if allies:
        roll_c = draw(seed, r, npc, "court") % 4
        chosen = allies[0]
        if roll_c == 0:
            court = {"type": "joint_project", "target": chosen}
        elif roll_c == 1:
            court = {"type": "aid", "target": chosen}
        elif roll_c == 2:
            candidates = list(HOUSES)
            candidates.sort(key=lambda c: (
                -candidate_score(state, npc, c)
                -candidate_score(state, chosen, c), c))
            court = {"type": "coalition", "target": chosen,
                     "candidate": candidates[0]}
    # A is a real negotiating partner, but never an automatically consenting
    # NPC. Its invitation and every permissible counter threshold are sealed
    # with this plan before the command is known.
    invitation = player_offer(state, seed, r, npc) if pact(state, npc, "A") else None
    if invitation is not None:
        court = {"type": "player_offer", "target": "A"}
    return {"action": action, "raid_target": opponents[0],
            "diplomacy_priorities": targets, "court": court,
            "player_offer": invitation}


def coalition_terms(state, a, b, candidate):
    """Symmetric concession prices, based solely on the opening public state."""
    rel = relationship(state, a, b)
    thresholds = {}
    for voter in (a, b):
        gap = max(candidate_score(state, voter, c) for c in HOUSES)
        gap -= candidate_score(state, voter, candidate)
        thresholds[voter] = max(
            1, gap + 4 - min(5, rel["capital"] // 2) + min(4, rel["tension"]))
    # Both signers quote the same support formula. The NPC requires its full
    # threshold; its capped compensation to the human is an offer, which the
    # human may decline. One gold is worth three and one influence worth two.
    return {"thresholds": thresholds,
            "required_value": thresholds[a],
            "gold_to_player": min(4, (thresholds[b] + 2) // 3)}


def player_offer(state, seed, r, sender):
    election = next_election(r)
    if election is None or not pact(state, sender, "A"):
        return None
    if _protected_commitment(state, ("A", sender), election):
        return None
    terms = {c: coalition_terms(state, sender, "A", c) for c in HOUSES}
    candidates = [c for c in HOUSES if terms[c]["required_value"] <= 12]
    if not candidates:
        return None
    candidate = max(candidates, key=lambda c: (
        candidate_score(state, sender, c) + candidate_score(state, "A", c),
        draw(seed, r, sender, "offer-candidate", c), -ord(c)))
    quote = terms[candidate]
    return {
        "type": "coalition_offer", "offer_id": "r%02d-%s-A" % (r, sender),
        "sender": sender, "target": "A", "candidate": candidate,
        "election_round": election, "expires_after_round": r,
        "pact_until": election, "participation_gold_each": 1,
        "gold_from_player": (quote["required_value"] + 2) // 3,
        "influence_from_player": 0, "gold_to_player": quote["gold_to_player"],
        "counter_terms": terms,
    }


def npc_plans(state, seed, r):
    return {h: npc_plan(state, seed, r, h) for h in NPCS}


def envoy(state, seed, r):
    allies = [h for h in NPCS if pact(state, "A", h)]
    expires = []
    for p in state["pacts"]:
        if "A" in (p["a"], p["b"]) and p["expires_after_round"] <= r + 1:
            expires.append((p["expires_after_round"],
                            p["b"] if p["a"] == "A" else p["a"]))
    if expires:
        expires.sort()
        return {"type": "renew_offer", "sender": expires[0][1],
                "gold_cost": 1, "until": r + 3}
    if allies and draw(seed, r, "envoy", "funds") % 4 == 0:
        return {"type": "request_funds", "sender": sorted(allies)[0],
                "gold_cost": 2}
    return None


def make_docket(state, seed):
    r = state["round"] + 1
    if r > 20 or state["status"] != "ongoing":
        raise ValueError("No further rounds")
    motion = MOTIONS[draw(seed, "motion", r) % len(MOTIONS)]
    event = EVENTS[draw(seed, "event", r) % len(EVENTS)]
    plans = npc_plans(state, seed, r)
    speaker = NPCS[draw(seed, r, "speaker") % 6]
    relation = relationship(state, "A", speaker)
    bond = "盟约仍在" if pact(state, "A", speaker) else "尚未缔盟"
    return {
        "version": VERSION, "game_id": state["game_id"], "round": r,
        "motion": motion, "motion_text": MOTION_TEXT[motion],
        "event": event, "event_text": EVENT_TEXT[event],
        "election": r in (4, 8, 12, 16, 20),
        "ai_plan_commit": digest(plans),
        "offers": [deepcopy(plans[h]["player_offer"]) for h in NPCS
                   if plans[h].get("player_offer") is not None],
        "envoy": envoy(state, seed, r),
        "houses": deepcopy(state["houses"]), "stability": state["stability"],
        "pacts": deepcopy(state["pacts"]),
        "trust_with_player": {x: trust(state, "A", x) for x in NPCS},
        "political_capital": {
            x: {f: relationship(state, "A", x)[f]
                for f in ("capital", "tension", "refusals")}
            for x in NPCS
        },
        "scene": {
            "title": "七王长桌，第%d幕" % r,
            "actor": speaker, "speaker": REPRESENTATIVES[speaker],
            "line": "%s。本轮讨论%s；预报%s。与曜誓家%s，政治资本%d、紧张度%d。" % (
                CHARACTER_LINES[speaker], MOTION_TEXT[motion], EVENT_TEXT[event],
                bond, relation["capital"], relation["tension"]),
        },
    }


def _int(value, lo, hi, name):
    if type(value) is not int or not lo <= value <= hi:
        raise ValueError("%s must be integer %d..%d" % (name, lo, hi))
    return value


def validate_command(state, docket, cmd):
    if type(cmd) is not dict:
        raise ValueError("Command must be a JSON object")
    allowed = {"action", "diplomacy", "court", "reply", "offer_reply",
               "vote", "sway", "bid", "candidate"}
    if set(cmd) - allowed:
        raise ValueError("Unknown command fields")
    action = cmd.get("action")
    dip = cmd.get("diplomacy", {"type": "none"})
    court = cmd.get("court", {"type": "none"})
    if not isinstance(action, dict) or action.get("type") not in ACTIONS:
        raise ValueError("Unknown main action")
    if not isinstance(dip, dict) or dip.get("type") not in (
        "none", "bribe", "propose", "threaten", "betray"):
        raise ValueError("Unknown diplomacy")
    if not isinstance(court, dict) or court.get("type") not in (
        "none", "joint_project", "aid", "request_aid", "renew",
        "request_backing", "whip_motion", "coalition"):
        raise ValueError("Unknown political action")
    if cmd.get("vote") not in ("yes", "no"):
        raise ValueError("Choose yes/no")
    reply = cmd.get("reply", "decline")
    if reply not in ("decline", "accept") or (reply == "accept" and docket["envoy"] is None):
        raise ValueError("Invalid envoy reply")
    sway = _int(cmd.get("sway", 0), 0, 2, "sway")
    bid = _int(cmd.get("bid", 0), 0, 3, "bid")
    if docket["election"]:
        if cmd.get("candidate") not in tuple(HOUSES):
            raise ValueError("Election requires candidate A..G")
    elif bid or "candidate" in cmd:
        raise ValueError("No election on this round")
    dtyp, ctyp = dip["type"], court["type"]
    dtarget = dip.get("target")
    ctarget = court.get("target")
    if dtyp != "none" and dtarget not in tuple(NPCS):
        raise ValueError("Diplomacy requires target B..G")
    if ctyp != "none" and ctarget not in tuple(NPCS):
        raise ValueError("Political action requires target B..G")
    if ctyp != "none" and not pact(state, "A", ctarget):
        raise ValueError("Political target must be allied at round start")
    if dtyp == "betray" and not pact(state, "A", dtarget):
        raise ValueError("Cannot betray a non-ally")
    if dtyp == "betray" and (dtarget == ctarget or (
        reply == "accept" and docket["envoy"]["sender"] == dtarget)):
        raise ValueError("Cannot betray and cooperate with same house")
    if dtyp == "propose" and pact_count(state, "A") >= 2:
        raise ValueError("No free pact slot")
    if dtyp == "bribe":
        _int(dip.get("amount"), 1, 4, "bribe amount")
    if ctyp == "renew":
        if not any({p["a"], p["b"]} == {"A", ctarget} and
                   p["expires_after_round"] <= docket["round"] + 1
                   for p in state["pacts"]):
            raise ValueError("Pact not in renewal window")
    if ctyp in ("aid", "request_aid") and court.get("kind") not in ("gold", "guards"):
        raise ValueError("Aid kind must be gold or guards")
    if ctyp == "request_aid" and state["favor"][ctarget] < 1:
        raise ValueError("No favor credit")
    offer_gold = offer_inf = 0
    if ctyp in ("request_backing", "whip_motion"):
        offer_gold = _int(court.get("offer_gold"), 1, 4, "offer_gold")
        offer_inf = _int(court.get("offer_influence"), 0, 2, "offer_influence")
        if type(court.get("use_favor", False)) is not bool:
            raise ValueError("use_favor must be bool")
        if court.get("use_favor") and state["favor"][ctarget] < 1:
            raise ValueError("Not enough favor")
        if ctyp == "request_backing" and next_election(docket["round"]) is None:
            raise ValueError("No future election")
    if ctyp == "coalition":
        if court.get("candidate") not in tuple(HOUSES):
            raise ValueError("Coalition needs candidate")
    if action["type"] in ("raid", "sabotage"):
        target = action.get("target")
        if target not in tuple(NPCS):
            raise ValueError("Hostile action requires target B..G")
        if action["type"] == "raid" and (
                state["houses"]["A"]["guards"] < state["houses"][target]["guards"] + 3):
            raise ValueError("Raid requires at least target guards + 3")
        if action["type"] == "sabotage" and state["houses"]["A"]["intel"] < 3:
            raise ValueError("Sabotage requires 3 intel")
    offer_reply = cmd.get("offer_reply")
    response_gold = response_influence = 0
    if offer_reply is not None:
        if type(offer_reply) is not dict or offer_reply.get("response") not in (
                "accept", "decline", "counter"):
            raise ValueError("Invalid offer_reply")
        invitation = next((o for o in docket.get("offers", [])
                           if o["offer_id"] == offer_reply.get("offer_id")), None)
        if invitation is None:
            raise ValueError("Unknown or expired offer_id")
        allowed_reply = {"offer_id", "response"}
        if offer_reply["response"] == "counter":
            allowed_reply |= {"candidate", "offer_gold", "offer_influence"}
            if offer_reply.get("candidate") not in tuple(HOUSES):
                raise ValueError("Counteroffer needs candidate A..G")
            response_gold = 1 + _int(offer_reply.get("offer_gold"), 0, 4, "offer_gold")
            response_influence = _int(offer_reply.get("offer_influence"), 0, 2, "offer_influence")
        elif offer_reply["response"] == "accept":
            response_gold = 1 + invitation["gold_from_player"]
            response_influence = invitation["influence_from_player"]
        if set(offer_reply) - allowed_reply:
            raise ValueError("Unknown offer_reply fields")
        if offer_reply["response"] != "decline":
            if dtyp == "betray" and dtarget == invitation["sender"]:
                raise ValueError("Cannot betray and accept the same house's offer")
            if ctyp in ("coalition", "request_backing"):
                raise ValueError("Choose one election negotiation per round")
    maincost = ACTIONS[action["type"]]
    dipcost = dip["amount"] if dtyp == "bribe" else int(dtyp == "propose")
    courtcost = (2 if ctyp in ("joint_project", "renew") or
                 (ctyp == "aid" and court["kind"] == "gold") else
                 1 if ctyp == "coalition" else offer_gold)
    replycost = docket["envoy"]["gold_cost"] if reply == "accept" else 0
    available = state["houses"]["A"]["gold"] + (5 if action["type"] == "tax" else 0)
    if available < maincost + dipcost + courtcost + replycost + response_gold:
        raise ValueError("Insufficient gold for combined command")
    escrow = sway + bid + response_influence + (1 + offer_inf if ctyp in
                           ("request_backing", "whip_motion") else 0)
    if state["houses"]["A"]["influence"] < escrow:
        raise ValueError("Insufficient influence for escrow")
    if ctyp == "aid" and court["kind"] == "guards" and (
        state["houses"]["A"]["guards"] == 0 and action["type"] != "muster"):
        raise ValueError("Insufficient guards")
    return {
        "action": deepcopy(action), "diplomacy": deepcopy(dip),
        "court": deepcopy(court), "reply": reply, "vote": cmd["vote"],
        "sway": sway, "bid": bid,
        **({"candidate": cmd["candidate"]} if docket["election"] else {}),
        **({"offer_reply": deepcopy(offer_reply)} if offer_reply is not None else {}),
    }


def _log(out, code, **data):
    actor = data.pop("actor", None)
    if actor is None:
        actor = next((data[key] for key in ("sender", "house", "voter", "a")
                      if data.get(key) in tuple(HOUSES)), None)
    out["events"].append({
        "event_id": "r%02d-e%03d" % (out.get("round", 0), len(out["events"]) + 1),
        "code": code, "actor": actor, **data})


def do_action(s, h, action, out, *, target=None):
    if action == "raid":
        return do_raid(s, h, target, out, fallback_tax=h != "A")
    if action == "sabotage":
        return do_sabotage(s, h, target, out)
    amount = ACTIONS.get(action)
    if amount is None:
        return
    if action not in ("tax", "pass") and not spend(s, h, "gold", amount):
        _log(out, "main_unaffordable", house=h, action=action)
        return
    if action == "tax":
        change(s, h, "gold", 5)
        change(s, h, "prestige", -1)
    elif action == "reform":
        change(s, h, "prestige", 2)
        stability(s, 1)
    elif action == "campaign":
        change(s, h, "prestige", 1)
        change(s, h, "influence", 2)
    elif action == "lobby":
        change(s, h, "influence", 3)
    elif action == "muster":
        change(s, h, "guards", 3)
    elif action == "spy":
        change(s, h, "intel", 3)
    _log(out, "main", house=h, action=action)


def do_raid(s, h, target, out, *, fallback_tax=True):
    if target not in tuple(HOUSES) or target == h:
        raise ValueError("Raid requires another house")
    if (s["houses"][h]["gold"] < 2 or
            s["houses"][h]["guards"] < s["houses"][target]["guards"] + 3):
        _log(out, "raid_rejected", house=h, target=target, reason="resources")
        if fallback_tax:
            do_action(s, h, "tax", out)
        return
    spend(s, h, "gold", 2)
    stolen = min(3, s["houses"][target]["gold"])
    spend(s, target, "gold", stolen)
    change(s, h, "gold", stolen)
    scandal_before = s["houses"][h]["scandal"]
    change(s, h, "scandal", 3)
    stability(s, -1)
    adjust_relation(s, h, target, trust_delta=-4, tension=2)
    _log(out, "raid", house=h, target=target, stolen=stolen,
         scandal_gained=s["houses"][h]["scandal"] - scandal_before)


def do_sabotage(s, h, target, out):
    if target not in tuple(HOUSES) or target == h:
        raise ValueError("Sabotage requires another house")
    if not spend(s, h, "intel", 3):
        _log(out, "sabotage_rejected", house=h, target=target, reason="intel")
        return
    influence_lost = min(3, s["houses"][target]["influence"])
    change(s, target, "influence", -influence_lost)
    scandal_before = s["houses"][h]["scandal"]
    change(s, h, "scandal", 2)
    adjust_relation(s, h, target, trust_delta=-3, tension=2)
    _log(out, "sabotage", house=h, target=target,
         influence_lost=influence_lost, gold_cost=0, intel_cost=3,
         scandal_gained=s["houses"][h]["scandal"] - scandal_before)


def form_pact(s, a, b, r, out, payer):
    if pact(s, a, b) or pact_count(s, a) >= 2 or pact_count(s, b) >= 2:
        return False
    if trust(s, a, b) < 1 or s["houses"][payer]["gold"] < 1:
        return False
    spend(s, payer, "gold", 1)
    change(s, b if payer == a else a, "gold", 1)
    s["pacts"].append({"a": min(a, b), "b": max(a, b),
                        "expires_after_round": r + 3})
    adjust_relation(s, a, b, trust_delta=1, capital=1, tension=-1)
    _log(out, "pact_formed", a=a, b=b, until=r + 3)
    return True


def do_diplomacy(s, r, dip, out):
    kind, target = dip["type"], dip.get("target")
    if kind == "none":
        return
    if kind == "bribe":
        n = dip["amount"]
        if not spend(s, "A", "gold", n):
            _log(out, "bribe_rejected", sender="A", target=target,
                 gold=n, reason="insufficient_gold")
            return
        change(s, target, "gold", n)
        adjust_relation(s, "A", target, trust_delta=min(n, 2))
        _log(out, "bribe", sender="A", target=target, gold=n)
    elif kind == "propose":
        if not form_pact(s, "A", target, r, out, "A"):
            _log(out, "pact_rejected", sender="A", target=target)
    elif kind == "threaten":
        if s["houses"]["A"]["guards"] >= s["houses"][target]["guards"] + 3:
            change(s, "A", "influence", 2)
            adjust_relation(s, "A", target, trust_delta=-2, tension=1)
            _log(out, "threat_succeeded", actor="A", target=target)
        else:
            change(s, "A", "prestige", -2)
            _log(out, "threat_failed", actor="A", target=target)
    elif kind == "betray":
        s["pacts"] = [p for p in s["pacts"] if {p["a"], p["b"]} != {"A", target}]
        change(s, "A", "scandal", 3)
        change(s, "A", "prestige", -2)
        adjust_relation(s, "A", target, trust_delta=-3, capital=-10, tension=3)
        _log(out, "betrayal", sender="A", target=target)
        _expire_contracts(s, out, reason="pact_broken")


def do_npc_diplomacy(s, r, h, plan, out):
    for target in plan["diplomacy_priorities"]:
        if not pact(s, h, target) and form_pact(s, h, target, r, out, h):
            return
    if s["houses"][h]["gold"] >= 3:
        candidates = [x for x in plan["diplomacy_priorities"] if not pact(s, h, x)]
        if candidates:
            target = candidates[0]
            spend(s, h, "gold", 1)
            change(s, target, "gold", 1)
            adjust_relation(s, h, target, trust_delta=1)
            _log(out, "npc_bribe", sender=h, target=target, gold=1)


def apply_reply(s, docket, reply, out):
    if reply != "accept" or docket["envoy"] is None:
        return
    invitation = docket["envoy"]
    target = invitation["sender"]
    if not pact(s, "A", target):
        _log(out, "envoy_rejected", actor="A", target=target, reason="pact_expired")
        return
    if not spend(s, "A", "gold", invitation["gold_cost"]):
        _log(out, "envoy_rejected", actor="A", target=target, reason="no_gold")
        return
    if invitation["type"] == "renew_offer":
        for p in s["pacts"]:
            if {p["a"], p["b"]} == {"A", target}:
                p["expires_after_round"] = max(
                    p["expires_after_round"], invitation["until"])
        adjust_relation(s, "A", target, trust_delta=1, capital=1, tension=-1)
        change(s, target, "gold", invitation["gold_cost"])
        _log(out, "envoy_renewed", actor="A", target=target, until=invitation["until"])
    else:
        change(s, target, "gold", invitation["gold_cost"])
        s["favor"][target] = min(2, s["favor"][target] + 1)
        adjust_relation(s, "A", target, capital=1, trust_delta=1)
        _log(out, "envoy_aid", actor="A", target=target)


def _expire_contracts(s, out, *, reason="pact_expired"):
    retained = []
    for contract in s["contracts"]:
        voter = contract["voter"]
        partner = contract.get("partner", contract["candidate"])
        if pact(s, voter, partner):
            retained.append(contract)
        else:
            _log(out, "contract_expired", voter=voter, partner=partner,
                 candidate=contract["candidate"],
                 election_round=contract["election_round"], reason=reason)
    s["contracts"] = retained


def _protected_commitment(s, voters, election):
    return any(c["voter"] in voters and c["election_round"] == election
               and c.get("origin") == "npc_player_offer" for c in s["contracts"])


def apply_offer_reply(s, docket, reply, out):
    """Resolve a presealed invitation; no terms are recalculated after actions."""
    invitations = docket.get("offers", [])
    chosen_id = reply.get("offer_id") if reply else None
    invitation = next((o for o in invitations if o["offer_id"] == chosen_id), None)
    for offer in invitations:
        _log(out, "npc_offer_issued", sender=offer["sender"], target="A",
             candidate=offer["candidate"], offer_id=offer["offer_id"],
             terms=deepcopy(offer))
        if offer["offer_id"] != chosen_id:
            _log(out, "npc_offer_expired", sender=offer["sender"], target="A",
                 offer_id=offer["offer_id"], reason="no_response")
    if invitation is None:
        if reply is not None:
            _log(out, "npc_offer_rejected", actor="A", offer_id=chosen_id,
                 reason="unknown_offer")
        return
    sender = invitation["sender"]
    r = docket["round"]
    response = reply["response"]
    if invitation["expires_after_round"] < r:
        _log(out, "npc_offer_expired", sender=sender, target="A",
             offer_id=chosen_id, reason="deadline")
        return
    if response == "decline":
        _record_refusal(s, "A", sender)
        _log(out, "npc_offer_declined", actor="A", sender=sender, target="A",
             offer_id=chosen_id, refusals=relationship(s, "A", sender)["refusals"],
             tension=relationship(s, "A", sender)["tension"])
        return
    candidate = (reply["candidate"] if response == "counter"
                 else invitation["candidate"])
    terms = invitation["counter_terms"][candidate]
    gold = reply["offer_gold"] if response == "counter" else invitation["gold_from_player"]
    influence = (reply["offer_influence"] if response == "counter"
                 else invitation["influence_from_player"])
    npc_gold = terms["gold_to_player"]
    value = 3 * gold + 2 * influence
    agreement = next((p for p in s["pacts"]
                      if {p["a"], p["b"]} == {"A", sender}), None)
    reason = None
    if agreement is None or agreement["expires_after_round"] < r:
        reason = "pact_expired"
    elif _protected_commitment(s, ("A", sender), invitation["election_round"]):
        reason = "already_committed"
    elif trust(s, "A", sender) < 2:
        reason = "trust"
    elif value < terms["required_value"]:
        reason = "below_sealed_threshold"
    elif (s["houses"]["A"]["gold"] < 1 + gold
          or s["houses"]["A"]["influence"] < influence
          or s["houses"][sender]["gold"] < 1 + npc_gold):
        reason = "resources"
    if reason:
        _record_refusal(s, "A", sender)
        _log(out, "npc_offer_rejected", actor=sender, sender=sender, target="A",
             offer_id=chosen_id, candidate=candidate, response=response,
             reason=reason, value=value, required=terms["required_value"])
        return
    # Validate all debits before any transfer; incoming funds cannot pay fees.
    spend(s, "A", "gold", 1 + gold)
    spend(s, "A", "influence", influence)
    spend(s, sender, "gold", 1 + npc_gold)
    change(s, sender, "gold", gold)
    change(s, sender, "influence", influence)
    change(s, "A", "gold", npc_gold)
    agreement["expires_after_round"] = max(
        agreement["expires_after_round"], invitation["pact_until"])
    adjust_relation(s, "A", sender, capital=2, tension=-1)
    for voter, partner in (("A", sender), (sender, "A")):
        sign_contract(s, voter, candidate, r, out, "npc_player_offer", partner=partner)
    _log(out, "npc_offer_accepted", actor=sender, sender=sender, target="A",
         offer_id=chosen_id, candidate=candidate, response=response,
         election_round=invitation["election_round"],
         pact_until=agreement["expires_after_round"],
         gold_from_player=gold, influence_from_player=influence,
         gold_to_player=npc_gold, participation_gold_each=1,
         value=value, required=terms["required_value"])


def joint_project(s, sender, target, out):
    if not pact(s, sender, target) or trust(s, sender, target) < 2:
        _log(out, "joint_rejected", sender=sender, target=target, reason="relation")
        return False
    if s["houses"][sender]["gold"] < 2 or s["houses"][target]["gold"] < 1:
        _log(out, "joint_rejected", sender=sender, target=target, reason="gold")
        return False
    spend(s, sender, "gold", 2)
    spend(s, target, "gold", 1)
    change(s, sender, "prestige", 1)
    change(s, sender, "influence", 1)
    change(s, target, "prestige", 1)
    stability(s, 1)
    capital_before = relationship(s, sender, target)["capital"]
    adjust_relation(s, sender, target, trust_delta=1, capital=2, tension=-1)
    _log(out, "joint_project", sender=sender, target=target,
         capital_gained=relationship(s, sender, target)["capital"] - capital_before)
    return True


def _record_refusal(s, a, b):
    rel = relationship(s, a, b)
    rel["refusals"] = min(8, rel["refusals"] + 1)
    if rel["refusals"] % 2 == 0:
        adjust_relation(s, a, b, tension=1)


def sign_contract(s, voter, candidate, r, out, label, *, partner=None):
    # Backing binds voter and nominee; a coalition binds its two signers,
    # even when they nominate one of themselves or an unrelated third house.
    partner = candidate if partner is None else partner
    election = next_election(r)
    if election is None or not pact(s, voter, partner):
        return False
    if _protected_commitment(s, (voter,), election):
        _log(out, "contract_rejected", voter=voter, candidate=candidate,
             partner=partner, reason="existing_player_offer")
        return False
    s["contracts"] = [
        x for x in s["contracts"]
        if not (x["voter"] == voter and x["election_round"] == election)]
    s["contracts"].append({
        "voter": voter, "candidate": candidate, "election_round": election,
        "origin": label, "partner": partner})
    _log(out, "backing_signed", voter=voter, candidate=candidate,
         election_round=election, origin=label, partner=partner)
    return True


def player_court(s, r, court, vote, out):
    kind = court["type"]
    if kind == "none":
        return
    t = court["target"]
    rel = relationship(s, "A", t)
    if not pact(s, "A", t):
        _log(out, "court_rejected", actor="A", type=kind, target=t, reason="not_allied")
        return
    if kind in ("coalition", "request_backing") and _protected_commitment(
            s, ("A", t), next_election(r)):
        _log(out, "court_rejected", actor="A", type=kind,
             target=t, reason="existing_player_offer")
        return
    if kind == "joint_project":
        joint_project(s, "A", t, out)
    elif kind == "renew":
        for p in s["pacts"]:
            if {p["a"], p["b"]} == {"A", t}:
                if (trust(s, "A", t) >= 3 and
                        p["expires_after_round"] <= r + 1 and
                        spend(s, "A", "gold", 2)):
                    change(s, t, "gold", 2)
                    p["expires_after_round"] = max(p["expires_after_round"], r + 3)
                    adjust_relation(s, "A", t, capital=1, tension=-1)
                    _log(out, "renewed", actor="A", target=t, until=p["expires_after_round"])
                else:
                    _log(out, "renew_rejected", actor="A", target=t)
    elif kind == "aid":
        if court["kind"] == "gold" and spend(s, "A", "gold", 2):
            change(s, t, "gold", 2)
            success = True
        elif court["kind"] == "guards" and spend(s, "A", "guards", 1):
            change(s, t, "guards", 1)
            success = True
        else:
            success = False
        if success:
            s["favor"][t] = min(2, s["favor"][t] + 1)
            adjust_relation(s, "A", t, trust_delta=1, capital=1, tension=-1)
            _log(out, "ally_aid", actor="A", target=t, kind=court["kind"])
        else:
            _log(out, "ally_aid_rejected", actor="A", target=t)
    elif kind == "request_aid":
        reserve = 3 if court["kind"] == "gold" else 2
        if s["favor"][t] and s["houses"][t][court["kind"]] >= reserve:
            s["favor"][t] -= 1
            qty = 2 if court["kind"] == "gold" else 1
            spend(s, t, court["kind"], qty)
            change(s, "A", court["kind"], qty)
            _log(out, "aid_redeemed", actor=t, target=t, kind=court["kind"], quantity=qty)
        else:
            _log(out, "aid_redemption_rejected", actor=t, target=t)
    elif kind in ("whip_motion", "request_backing"):
        gold, inf = court["offer_gold"], court["offer_influence"]
        favor = 1 if court.get("use_favor", False) else 0
        # Gold and influence were reserved by validation, but only the inquiry
        # fee was actually charged in the pre-action escrow.
        if kind == "request_backing":
            required = backing_threshold(s, t, "A")
            value = 3 * gold + 2 * inf + 4 * favor
        else:
            required = max(1, 3 - rel["capital"] // 2 + rel["tension"])
            value = 2 * gold + 2 * inf + 3 * favor
        if (trust(s, "A", t) < 2 or value < required or
                s["houses"]["A"]["gold"] < gold or
                s["houses"]["A"]["influence"] < inf):
            _record_refusal(s, "A", t)
            _log(out, "politics_offer_rejected", actor=t, target=t, type=kind,
                 value=value, required=required)
            return
        spend(s, "A", "gold", gold)
        spend(s, "A", "influence", inf)
        change(s, t, "gold", gold)
        change(s, t, "influence", inf)
        if favor:
            s["favor"][t] -= 1
        adjust_relation(s, "A", t, capital=1, tension=-1)
        if kind == "request_backing":
            sign_contract(s, t, "A", r, out, "player_offer")
        else:
            out["forced_council"][t] = vote
            _log(out, "motion_whipped", actor=t, target=t, vote=vote)
        _log(out, "politics_offer_accepted", actor=t, target=t, type=kind,
             value=value, required=required)
    elif kind == "coalition":
        candidate = court["candidate"]
        best = max(candidate_score(s, t, c) for c in HOUSES)
        diff = best - candidate_score(s, t, candidate)
        if (s["houses"]["A"]["gold"] >= 1 and
                s["houses"][t]["gold"] >= 1 and
                diff <= rel["capital"] + 2):
            spend(s, "A", "gold", 1)
            spend(s, t, "gold", 1)
            adjust_relation(s, "A", t, capital=2, tension=-1)
            for voter, partner in (("A", t), (t, "A")):
                sign_contract(s, voter, candidate, r, out, "player_coalition",
                              partner=partner)
            _log(out, "coalition_formed", a="A", b=t, candidate=candidate)
        else:
            _record_refusal(s, "A", t)
            _log(out, "coalition_rejected", actor=t, target=t, candidate=candidate)


def npc_court(s, r, actor, plan, out):
    c = plan["court"]
    if c["type"] in ("none", "player_offer"):
        # Player offers are resolved only by apply_offer_reply after consent.
        return
    target = c["target"]
    if not pact(s, actor, target):
        return
    if target == "A":
        # Legacy internal NPC action records cannot imply player consent.
        _log(out, "court_rejected", sender=actor, target=target,
             reason="player_reply_required")
        return
    if c["type"] == "coalition" and _protected_commitment(
            s, (actor, target), next_election(r)):
        _log(out, "court_rejected", sender=actor, target=target,
             reason="existing_player_offer")
        return
    if c["type"] == "joint_project":
        joint_project(s, actor, target, out)
    elif c["type"] == "aid":
        if s["houses"][actor]["gold"] >= 8 and s["houses"][target]["gold"] <= 5:
            spend(s, actor, "gold", 2)
            change(s, target, "gold", 2)
            adjust_relation(s, actor, target, capital=1, tension=-1)
            _log(out, "npc_allied_aid", sender=actor, target=target)
    elif c["type"] == "coalition":
        candidate = c["candidate"]
        rel = relationship(s, actor, target)
        if (s["houses"][actor]["gold"] < 1 or
                s["houses"][target]["gold"] < 1 or
                trust(s, actor, target) < 2):
            return
        best = max(candidate_score(s, target, h) for h in HOUSES)
        diff = best - candidate_score(s, target, candidate)
        if diff > rel["capital"] + 2:
            _record_refusal(s, actor, target)
            return
        spend(s, actor, "gold", 1)
        spend(s, target, "gold", 1)
        adjust_relation(s, actor, target, capital=2, tension=-1)
        # The nomination is binding for both politicians, not for the nominee.
        for voter, partner in ((actor, target), (target, actor)):
            sign_contract(s, voter, candidate, r, out, "npc_coalition",
                          partner=partner)
        _log(out, "npc_coalition", a=actor, b=target, candidate=candidate)


def _apply_motion(s, motion, out):
    houses = s["houses"]
    if motion == "relief":
        for h in HOUSES: change(s, h, "gold", -1)
        stability(s, 2)
    elif motion == "conscription":
        for h in HOUSES: change(s, h, "guards", 1)
        stability(s, -1)
    elif motion == "academy":
        for h in HOUSES:
            change(s, h, "gold", -1)
            change(s, h, "intel", 1)
        stability(s, 1)
    elif motion == "embargo":
        change(s, select_max(s, "gold"), "gold", -3)
        stability(s, 1)
    elif motion == "amnesty":
        change(s, select_max(s, "scandal"), "scandal", -2)
        stability(s, 1)
    elif motion == "festival":
        for h in HOUSES:
            change(s, h, "gold", -1)
            change(s, h, "prestige", 1)
        stability(s, 1)
    elif motion == "fortify":
        change(s, select_max(s, "guards"), "guards", 2)
        stability(s, -1)
    elif motion == "common_tax":
        richest = select_max(s, "gold")
        poorest = select_min(s, "gold")[0]
        change(s, richest, "gold", -2)
        change(s, poorest, "gold", 2)
    elif motion == "reform_bill":
        for h in HOUSES:
            change(s, h, "gold", -2)
            change(s, h, "prestige", 1)
        stability(s, 1)
    elif motion == "impeachment":
        h = select_max(s, "scandal")
        change(s, h, "prestige", -2)
        change(s, h, "scandal", -1)
        stability(s, 1)
    _log(out, "motion_enacted", motion=motion)


def council_value(s, motion, npc, goals):
    before = s["houses"][npc].copy()
    tmp = deepcopy(s)
    base = tmp["stability"]
    _apply_motion(tmp, motion, {"events": []})
    after = tmp["houses"][npc]
    val = (after["gold"] - before["gold"]
           + 2 * (after["prestige"] - before["prestige"])
           + 2 * (after["guards"] - before["guards"])
           + after["intel"] - before["intel"]
           - 2 * (after["scandal"] - before["scandal"]))
    stability_delta = tmp["stability"] - base
    val += (3 if goals[npc] == "pacifier" else 1) * stability_delta
    if goals[npc] == "warlord" and motion in ("conscription", "fortify"):
        val += 3
    if goals[npc] == "hoarder" and motion in ("common_tax", "embargo"):
        val -= 2
    if goals[npc] == "tribune" and motion in ("festival", "reform_bill"):
        val += 2
    return val


def _apply_event(s, event, seed, r, out):
    if event == "harvest":
        for h in HOUSES: change(s, h, "gold", 2)
        stability(s, 1)
    elif event == "drought":
        for h in HOUSES: change(s, h, "gold", -1)
        stability(s, -1)
    elif event == "plague":
        for h in HOUSES: change(s, h, "guards", -1)
        stability(s, -2)
    elif event == "omen":
        change(s, select_max(s, "prestige"), "influence", 1)
        stability(s, 1)
    elif event == "trade":
        for h in select_min(s, "gold", 2): change(s, h, "gold", 2)
    elif event == "spy_leak":
        change(s, select_max(s, "intel"), "scandal", 1)
        stability(s, -1)
    elif event == "reform_wave":
        for h in select_min(s, "prestige", 2): change(s, h, "prestige", 1)
        stability(s, 1)
    elif event == "border_incursion":
        change(s, select_max(s, "guards"), "guards", -2)
        stability(s, -1)
    elif event == "riot":
        change(s, select_max(s, "gold"), "gold", -2)
        stability(s, -2)
    elif event == "coup":
        change(s, HOUSES[draw(seed, r, "coup_target") % 7], "guards", -1)
        stability(s, -2)
    _log(out, "world_event", event=event, stability=s["stability"])


def goal_met(s, h, goal):
    person = s["houses"][h]
    if goal == "player_mission":
        return pact_count(s, "A") >= 2 and person["scandal"] <= 2
    if goal == "kingmaker": return person["crowns"] >= 2
    if goal == "warlord": return person["guards"] >= 12
    if goal == "tribune": return person["prestige"] >= 12
    if goal == "spymaster": return person["intel"] >= 9 and person["scandal"] <= 2
    if goal == "pacifier": return s["stability"] >= 8 and person["scandal"] <= 1
    if goal == "hoarder": return person["gold"] >= 15
    return False


def standings(s, goals):
    scores = {}
    for h in HOUSES:
        x = s["houses"][h]
        scores[h] = (x["gold"] + x["guards"] + x["influence"] +
                     2 * x["prestige"] + x["intel"] +
                     5 * x["crowns"] - 3 * x["scandal"] +
                     (12 if goal_met(s, h, goals[h]) else 0))
    return dict(sorted(scores.items(), key=lambda item: (-item[1], item[0])))


def _finish(s, goals, out):
    s["scores"] = standings(s, goals)
    order = list(s["scores"])
    s["winner"] = order[0]
    s["goal_results"] = {h: goal_met(s, h, goals[h]) for h in HOUSES}
    a = s["houses"]["A"]
    hard = (s["stability"] <= 0 or a["prestige"] <= 0 or a["scandal"] >= 8)
    lead = s["scores"]["A"] - max(s["scores"][h] for h in NPCS)
    perfect = (not hard and order[0] == "A" and lead >= 22 and
               a["crowns"] >= 3 and a["scandal"] == 0 and
               s["stability"] >= 7 and goal_met(s, "A", goals["A"]))
    s["status"] = ("hard_failure" if hard else
                   "perfect_victory" if perfect else
                   "victory" if order[0] == "A" else "defeat")
    out["final"] = {
        "status": s["status"], "scores": s["scores"],
        "goals_met": s["goal_results"], "winner": s["winner"],
        "lead": lead, "player_crowns": a["crowns"],
        "stability": s["stability"],
    }


def event_dialogue(event):
    """Render only recorded facts, with an explicit actor and source event."""
    code = event["code"]
    actor = event["actor"]
    text = None
    if code == "pact_formed":
        text = "我与%s缔结盟约，期限至第%d轮结束。" % (
            event["b"], event["until"])
    elif code in ("npc_coalition", "coalition_formed"):
        text = "我与%s共同提名%s；承诺由两位签署者承担。" % (
            event["b"], event["candidate"])
    elif code == "backing_signed":
        text = "我承诺在第%d轮支持%s，签约伙伴为%s。" % (
            event["election_round"], event["candidate"], event["partner"])
    elif code == "contract_honored":
        text = "我履行了对%s的票约，本轮选票投给%s。" % (
            event["partner"], event["candidate"])
    elif code == "contract_expired":
        text = "与%s的盟约已失效，原定第%d轮支持%s的票约随之解除。" % (
            event["partner"], event["election_round"], event["candidate"])
    elif code == "player_broke_coalition":
        text = "我改投%s，违背了与%s共同支持%s的承诺。" % (
            event["actual_candidate"], event["partner"], event["candidate"])
    elif code == "joint_project":
        text = "我与%s完成联合项目，双方政治资本增加%d。" % (
            event["target"], event["capital_gained"])
    elif code == "npc_offer_issued":
        terms = event["terms"]
        text = "我邀请曜誓家共同提名%s：双方各付1金，曜誓家另付%d金、%d影响，我另付%d金；盟约延至第%d轮。" % (
            event["candidate"], terms["gold_from_player"],
            terms["influence_from_player"], terms["gold_to_player"], terms["pact_until"])
    elif code == "npc_offer_accepted":
        text = "与曜誓家的%s已成交：共同支持%s，按封存条件结算，票约在第%d轮兑现。" % (
            "反提案" if event["response"] == "counter" else "邀约",
            event["candidate"], event["election_round"])
    elif code == "npc_offer_declined":
        text = "我拒绝了%s的邀约；累计拒绝%d次，当前紧张度%d。" % (
            event["sender"], event["refusals"], event["tension"])
    elif code in ("npc_offer_rejected", "npc_offer_expired"):
        text = "邀约%s未成交，记录原因为%s。" % (event["offer_id"], event["reason"])
    elif code in ("politics_offer_accepted", "politics_offer_rejected"):
        text = "报价价值%d，所需门槛%d；这项%s已经%s。" % (
            event["value"], event["required"], event["type"],
            "接受" if code.endswith("accepted") else "拒绝")
    elif code == "coalition_rejected":
        text = "本轮未能同意共同提名%s。" % event["candidate"]
    elif code == "bribe_rejected":
        text = "向%s支付%d金未成功：实际余额不足，未转账也未增加信任。" % (
            event["target"], event["gold"])
    elif code == "raid":
        text = "我袭击%s，夺取%d金；丑闻与双方关系代价已入账。" % (
            event["target"], event["stolen"])
    elif code == "sabotage":
        text = "我消耗3情报破坏%s，使其影响力减少%d，自身丑闻增加%d。" % (
            event["target"], event["influence_lost"], event["scandal_gained"])
    elif code == "world_event":
        text = "%s。结算后帝国稳定为%d。" % (
            EVENT_TEXT[event["event"]], event["stability"])
    elif code == "motion_enacted":
        text = "议会通过了%s。" % MOTION_TEXT[event["motion"]]
    if text is None:
        return None
    if actor is not None:
        text = CHARACTER_LINES[actor] + "。" + text
    return {"actor": actor,
            "speaker": REPRESENTATIVES[actor] if actor else "议会书记官",
            "text": text, "event_id": event["event_id"], "event_code": code}


def talk(state, target, text):
    """Non-advancing dialogue from public state; never a numerical promise.

    This API deliberately takes neither the secret seed nor hidden goals. It
    does not search arbitrary state fields or quote goal-dependent live scores.
    """
    if target not in tuple(NPCS):
        raise ValueError("Talk target must be B..G")
    if type(text) is not str or not text.strip() or len(text) > 1000:
        raise ValueError("Talk text must contain 1..1000 characters")
    rel = relationship(state, "A", target)
    agreement = next((p for p in state["pacts"]
                      if {p["a"], p["b"]} == {"A", target}), None)
    lines = [CHARACTER_LINES[target] + "。"]
    if any(word in text.lower() for word in ("盟", "约", "alliance", "pact")):
        lines.append("我们盟约持续至第%d轮结束。" % agreement["expires_after_round"]
                     if agreement else "我们目前没有生效的盟约。")
    if any(word in text.lower() for word in ("票", "提名", "vote", "支持", "承诺")):
        contracts = [c for c in state["contracts"]
                     if c["voter"] == target and c.get("partner", c["candidate"]) == "A"]
        lines.extend("正式记录：第%d轮支持%s。" % (c["election_round"], c["candidate"])
                     for c in contracts)
        if not contracts:
            lines.append("我尚未向曜誓家签署选举票约。")
    if any(word in text.lower() for word in ("拒", "关系", "信任", "trust", "refus")):
        lines.append("我们的信任为%d，政治资本%d，紧张度%d，拒绝记录%d次。" % (
            rel["trust"], rel["capital"], rel["tension"], rel["refusals"]))
    last = state.get("last_outcome")
    if last and any(word in text.lower() for word in ("世界", "事件", "议案", "发生", "event", "motion")):
        lines.append("上一轮议案是%s；发生的世界事件是%s。" % (
            MOTION_TEXT[last["motion"]], EVENT_TEXT[last["event"]]))
    if len(lines) == 1:
        lines.append("帝国稳定为%d。我们的政治资本为%d，紧张度为%d。" % (
            state["stability"], rel["capital"], rel["tension"]))
    lines.append("这只是谈话，不推进回合、不转移资源，也不产生正式承诺；数值协议须用回合指令签署。")
    return {"round": state["round"], "target": target,
            "speaker": REPRESENTATIVES[target], "query": text,
            "text": "".join(lines), "binding": False, "advances_round": False}


def execute_round(state, secret, raw_command):
    """Deterministic round result. Does not mutate caller's state or do any I/O."""
    s = deepcopy(state)
    r = s["round"] + 1
    seed = secret["seed"]
    docket = make_docket(s, seed)
    command = validate_command(s, docket, raw_command)
    plans = npc_plans(s, seed, r)
    if digest(plans) != docket["ai_plan_commit"]:
        raise RuntimeError("NPC precommit mismatch")
    out = {
        "round": r, "version": VERSION, "command": command,
        "ai_plan_commit": docket["ai_plan_commit"], "ai_plans": plans,
        "motion": docket["motion"], "event": docket["event"],
        "events": [], "forced_council": {}, "council_votes": {},
        "election_votes": {},
    }
    court = command["court"]
    court_escrow = (1 if court["type"] in ("whip_motion", "request_backing")
                    else 0)
    escrow = command["sway"] + command["bid"] + court_escrow
    if not spend(s, "A", "influence", escrow):
        raise RuntimeError("Validated influence escrow unavailable")
    _log(out, "escrow", actor="A", influence=escrow)
    do_action(s, "A", command["action"]["type"], out,
              target=command["action"].get("target"))
    for npc in NPCS:
        plan = plans[npc]
        if plan["action"] == "raid":
            do_raid(s, npc, plan["raid_target"], out)
        else:
            do_action(s, npc, plan["action"], out)
    do_diplomacy(s, r, command["diplomacy"], out)
    for npc in NPCS:
        do_npc_diplomacy(s, r, npc, plans[npc], out)
    apply_reply(s, docket, command["reply"], out)
    apply_offer_reply(s, docket, command.get("offer_reply"), out)
    player_court(s, r, court, command["vote"], out)
    for npc in NPCS:
        npc_court(s, r, npc, plans[npc], out)
    # Council: an allied NPC's close call may follow the player, but not always.
    count_yes = count_no = 0
    for h in HOUSES:
        if h == "A":
            ballot = command["vote"]
            weight = 1 + command["sway"]
        else:
            val = council_value(s, docket["motion"], h, secret["goals"])
            ballot = ("yes" if val > 0 else "no" if val < 0 else
                      ("yes" if draw(seed, r, h, "council_tie") % 2 else "no"))
            if pact(s, "A", h) and abs(val) <= 1:
                ballot = command["vote"]
            ballot = out["forced_council"].get(h, ballot)
            extra = int(s["houses"][h]["influence"] >= 7 and abs(val) >= 4)
            if extra:
                spend(s, h, "influence", 1)
            weight = 1 + extra
        out["council_votes"][h] = {"vote": ballot, "weight": weight}
        if ballot == "yes": count_yes += weight
        else: count_no += weight
    out["council_yes"] = count_yes
    out["council_no"] = count_no
    out["council_enacted"] = count_yes > count_no
    if out["council_enacted"]:
        _apply_motion(s, docket["motion"], out)
    # Public actions in alignment are a real, modest source of political capital.
    for p in s["pacts"]:
        a, b = p["a"], p["b"]
        if out["council_votes"][a]["vote"] == out["council_votes"][b]["vote"]:
            adjust_relation(s, a, b, capital=1)
    if docket["election"]:
        totals = {h: 0 for h in HOUSES}
        for voter in HOUSES:
            ballot = (command["candidate"] if voter == "A"
                      else promised(s, voter, r) or favorite(s, voter))
            if voter == "A":
                weight = 1 + command["bid"]
            else:
                influence = s["houses"][voter]["influence"]
                extra = (min(3, influence // 3) if voter == ballot else
                         int(influence >= 6))
                spend(s, voter, "influence", extra)
                weight = 1 + extra
            out["election_votes"][voter] = {
                "candidate": ballot, "weight": weight,
                "contract": bool(promised(s, voter, r)),
            }
            totals[ballot] += weight
        out["election_totals"] = totals
        winner = max(HOUSES, key=lambda h: (
            totals[h], s["houses"][h]["prestige"],
            s["houses"][h]["influence"], -s["houses"][h]["scandal"], -ord(h)))
        out["election_winner"] = winner
        change(s, winner, "crowns", 1)
        change(s, winner, "prestige", 2)
        change(s, winner, "gold", 2)
        rewarded_contracts = set()
        for contract in list(s["contracts"]):
            if contract["election_round"] != r:
                continue
            voter, chosen = contract["voter"], contract["candidate"]
            partner = contract.get("partner", chosen)
            if not pact(s, voter, partner):
                continue
            if voter == "A" and chosen != command["candidate"]:
                change(s, "A", "scandal", 2)
                adjust_relation(s, "A", partner, trust_delta=-2, tension=2)
                _log(out, "player_broke_coalition", actor="A", partner=partner,
                     candidate=chosen, actual_candidate=command["candidate"])
            elif out["election_votes"][voter]["candidate"] == chosen:
                # The reciprocal coalition records describe one agreement;
                # do not double its shared relationship reward.
                reward_key = (pair(voter, partner), chosen)
                if reward_key not in rewarded_contracts:
                    adjust_relation(s, voter, partner, capital=2, tension=-1)
                    rewarded_contracts.add(reward_key)
                _log(out, "contract_honored", voter=voter, partner=partner,
                     candidate=chosen, election_round=r)
        # Distinguish coalition-wide alignment from rival uncoordinated ballots.
        for p in s["pacts"]:
            a, b = p["a"], p["b"]
            av = out["election_votes"][a]["candidate"]
            bv = out["election_votes"][b]["candidate"]
            if av == bv:
                adjust_relation(s, a, b, capital=1, tension=-1)
            elif a != "A" and b != "A":
                adjust_relation(s, a, b, tension=1)
        s["contracts"] = [x for x in s["contracts"] if x["election_round"] > r]
        _log(out, "election", winner=winner, totals=totals)
    _apply_event(s, docket["event"], seed, r, out)
    for h in HOUSES:
        if not spend(s, h, "gold", 1):
            change(s, h, "prestige", -1)
            _log(out, "unpaid_upkeep", house=h)
    # The endgame amendment: a pact expiring after round 20 counts at the
    # end-of-round-20 final mission check. All earlier rounds clean up normally.
    if r < 20:
        expired = [p for p in s["pacts"] if p["expires_after_round"] <= r]
        if expired:
            _log(out, "pacts_expired", pairs=[pair(p["a"], p["b"]) for p in expired])
        s["pacts"] = [p for p in s["pacts"] if p["expires_after_round"] > r]
        _expire_contracts(s, out)
    s["round"] = r
    if r == 20:
        _finish(s, secret["goals"], out)
    elif s["stability"] == 0 or s["houses"]["A"]["scandal"] >= 8:
        s["status"] = "hard_failure"
    # Live scores must not reveal whether a hidden objective currently earns
    # its 12-point bonus. Terminal archives may show full goal-based scoring.
    s["scores"] = standings(s, secret["goals"] if s["status"] != "ongoing"
                            else {h: "unrevealed" for h in HOUSES})
    s["last_outcome"] = {
        "round": r, "motion": docket["motion"],
        "event": docket["event"], "council_yes": count_yes,
        "council_no": count_no,
        "election_winner": out.get("election_winner"),
    }
    out["status"] = s["status"]
    out["scores"] = s["scores"]
    out["political_relations"] = deepcopy(s["relations"])
    out["scene"] = {
        "opening": docket["scene"],
        "exchange": [line for event in out["events"]
                     if (line := event_dialogue(event)) is not None],
    }
    return s, out
