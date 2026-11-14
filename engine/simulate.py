"""LocalSim 확산 엔진 (영업시간·요일·연령대 반영)
동네 + 커뮤니티 관계망, 유동인구·직장인구(낮 방문자), 경쟁 점포 수, 확산(1단계=1주), 방문 계산,
12주 방문객 곡선(관심 식음 + 재방문), 추가 홍보 타이밍, 손익분기, 시드 여러 개 평균

실행 (저장소 맨 위에서):
    python engine/simulate.py                                   # 후보 3개 동 (프론트용)
    python engine/simulate.py --all-dongs --out outputs/validation_run   # 전체 동 (검증용)
    python engine/simulate.py --dongs 1144071000 1168064000      # 원하는 동만

입력: {data}/personas.csv, reactions.json, (있으면) dongs.geojson, dong_stats.csv
출력: {out}/result_{동코드}.json, (후보 모드일 때) {out}/edges.json
가중치: config/weights.json

서울 전체 시민 중 '가게 반경 sim_radius_m 안에 사는 사람'만 골라 동마다 따로 계산한다.
"""
import argparse
import csv
import json
import math
import random
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CANDIDATES = {"1144069000": "망원1동", "1144071000": "연남동", "1144066000": "서교동"}


# ---------- 데이터 읽기 ----------
def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_personas(folder):
    with open(folder / "personas.csv", encoding="utf-8-sig") as f:
        return [{"id": int(r["id"]), "dong_code": str(r["dong_code"]), "dong": r["dong"],
                 "group": r["group"], "lat": float(r["lat"]), "lng": float(r["lng"])}
                for r in csv.DictReader(f)]


def load_reactions(folder):
    path = folder / "reactions.json"
    return {r["group"]: r for r in load_json(path)} if path.exists() else {}


def load_dong_stats(folder):
    """동별 유동인구·직장인구·점포 수. 파일이 없으면 빈 dict (기존 방식으로 동작)"""
    path = folder / "dong_stats.csv"
    if not path.exists():
        return {}
    with open(path, encoding="utf-8-sig") as f:
        return {str(r["dong_code"]): {"floating_pop": float(r["floating_pop"]),
                                      "worker_pop": float(r["worker_pop"]),
                                      "store_count": float(r["store_count"]),
                                      "is_estimated": int(float(r.get("is_estimated", 0) or 0)),
                                      "avg_price": implied_price(r),
                                      "flow": {k: num(r.get(k)) for k in FLOW_COLS}}
                for r in csv.DictReader(f)}


FLOW_HOURS = {"flow_06_11": (6, 11), "flow_11_14": (11, 14), "flow_14_17": (14, 17),
              "flow_17_21": (17, 21), "flow_21_24": (21, 24)}
FLOW_AGE = {"20대이하": "flow_age_20_under", "30대": "flow_age_30", "40대": "flow_age_40", "50대이상": "flow_age_50_over"}
FLOW_COLS = list(FLOW_HOURS) + list(FLOW_AGE.values()) + ["flow_weekday", "flow_weekend"]
DAYS = ["월", "화", "수", "목", "금", "토", "일"]


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def overlap(a, b, c, d):
    """[a,b) 와 [c,d) 시간 겹침 (시간 단위)"""
    return max(0.0, min(b, d) - max(a, c))


def hours_factor(st, open_hours, w):
    """영업시간에 지나가는 사람 비율을, 기준 영업시간(w['reference_hours'])일 때와 비교한 배수.
    저녁 영업이면 밤에 붐비는 동이, 낮 영업이면 오피스 상권이 유리해진다. 입력이 없으면 1"""
    flow = (st or {}).get("flow") or {}
    if not open_hours or not all(flow.get(k) for k in FLOW_HOURS):
        return 1.0, 1.0

    def covered(h0, h1):
        return sum(flow[k] * overlap(h0, h1, a, b) / (b - a) for k, (a, b) in FLOW_HOURS.items())

    r0, r1 = w["reference_hours"]
    h0, h1 = open_hours
    f_float = covered(h0, h1) / max(covered(r0, r1), 1e-9)
    w0, w1 = w["work_hours"]  # 직장인은 근무시간에만 근처에 있음
    f_work = overlap(h0, h1, w0, w1) / max(overlap(r0, r1, w0, w1), 1e-9)
    return f_float, f_work


def days_factor(st, open_days):
    """쉬는 날을 빼면 일주일 손님 중 얼마나 남는지 (평일은 평일 유동인구/5, 주말은 주말 유동인구/2로 하루씩 계산)"""
    flow = (st or {}).get("flow") or {}
    wd, we = flow.get("flow_weekday"), flow.get("flow_weekend")
    if not wd or not we:
        return (len(open_days) / 7 if open_days else 1.0), None
    daily = {d: (wd / 5 if i < 5 else we / 2) for i, d in enumerate(DAYS)}
    days = open_days or DAYS
    return sum(daily[d] for d in days if d in daily) / sum(daily.values()), we / (wd + we)


def implied_price(r, min_tx=1000):
    """동 평균 객단가 = 카페 월매출 / 월 결제 건수 (결제가 너무 적은 동은 None)"""
    try:
        sales, tx = float(r["cafe_monthly_sales_avg_krw"]), float(r["cafe_monthly_transactions_avg"])
    except (KeyError, TypeError, ValueError):
        return None
    return sales / tx if tx >= min_tx else None


def to01(score):
    """1~5점 -> 0~1"""
    return max(0.0, min(1.0, (float(score) - 1) / 4))


def distance_m(lat1, lng1, lat2, lng2):
    """서울 범위에서는 평면 근사로 충분"""
    dy = (lat1 - lat2) * 111_000
    dx = (lng1 - lng2) * 111_000 * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot(dx, dy)


def make_grid(people, radius_m):
    cell = radius_m / 111_000
    grid = defaultdict(list)
    for p in people:
        grid[(int(p["lat"] // cell), int(p["lng"] // cell))].append(p)
    return grid, cell


def near(p, grid, cell, radius_m):
    """p 반경 radius_m 안 사람들 (자기 자신 제외) -> [(거리, 사람)]"""
    ci, cj = int(p["lat"] // cell), int(p["lng"] // cell)
    out = []
    for di in (-1, 0, 1):
        for dj in (-1, 0, 1):
            for q in grid.get((ci + di, cj + dj), ()):
                if q["id"] != p["id"]:
                    d = distance_m(p["lat"], p["lng"], q["lat"], q["lng"])
                    if d <= radius_m:
                        out.append((d, q))
    return out


# ---------- 관계망 ----------
def build_neighbor_edges(people, radius_m, max_neighbors):
    """서로 radius_m 안에 사는 사람끼리 연결 (사람당 가까운 순 최대 max_neighbors명)"""
    grid, cell = make_grid(people, radius_m)
    edges = set()
    for p in people:
        cands = sorted(near(p, grid, cell, radius_m), key=lambda x: (x[0], x[1]["id"]))
        for _, q in cands[:max_neighbors]:
            edges.add((min(p["id"], q["id"]), max(p["id"], q["id"])))
    return [{"from": a, "to": b, "type": "동네"} for a, b in sorted(edges)]


def build_community_edges(people, reactions, links_per_person, radius_by_community, seed):
    """같은 커뮤니티 이용자끼리 사람당 무작위 links_per_person명과 연결.
    radius_by_community에 반경이 있는 커뮤니티(예: 당근)는 그 거리 안 회원끼리만."""
    rng = random.Random(f"community-{seed}")
    members = defaultdict(list)
    for p in people:
        for c in reactions.get(p["group"], {}).get("community", []):
            if c != "없음":
                members[c].append(p)

    edges = set()
    for c in sorted(members):
        pool = sorted(members[c], key=lambda p: p["id"])
        radius = radius_by_community.get(c)
        if radius:
            grid, cell = make_grid(pool, radius)
        for p in pool:
            if radius:
                cand = sorted((q for _, q in near(p, grid, cell, radius)), key=lambda q: q["id"])
                picks = rng.sample(cand, min(links_per_person, len(cand)))
            else:
                k = min(links_per_person, len(pool) - 1)
                picks, tried = [], set()
                while len(picks) < k:
                    q = pool[rng.randrange(len(pool))]
                    if q["id"] != p["id"] and q["id"] not in tried:
                        tried.add(q["id"])
                        picks.append(q)
            for q in picks:
                a, b = sorted((p["id"], q["id"]))
                edges.add((a, b, c))
    return [{"from": a, "to": b, "type": t} for a, b, t in sorted(edges)]


# ---------- 가게 위치 ----------
STORE_OVERRIDE = {}  # 사용자가 지도에서 찍은 가게 자리 {동코드: (위도, 경도)}


def store_point(dong_code, people, geo):
    """가게 위치 = 사용자가 찍은 자리가 있으면 그것, 없으면 후보 동의 중심점 (경계 파일 우선, 없으면 그 동 주민 평균 좌표)"""
    if dong_code in STORE_OVERRIDE:
        return STORE_OVERRIDE[dong_code]
    if geo:
        for feat in geo.get("features", []):
            if str(feat["properties"].get("adm_cd2")) == dong_code:
                coords = feat["geometry"]["coordinates"]
                ring = coords[0][0] if feat["geometry"]["type"] == "MultiPolygon" else coords[0]
                return (sum(c[1] for c in ring) / len(ring), sum(c[0] for c in ring) / len(ring))
    local = [p for p in people if p["dong_code"] == dong_code] or people
    return (sum(p["lat"] for p in local) / len(local), sum(p["lng"] for p in local) / len(local))


def local_people(dong_code, people, geo, radius_m):
    """가게 반경 radius_m 안에 사는 시민 + 그 동 주민 전원"""
    slat, slng = store_point(dong_code, people, geo)
    return [p for p in people
            if p["dong_code"] == dong_code or distance_m(p["lat"], p["lng"], slat, slng) <= radius_m]


# ---------- 확산 ----------
def coin(seed, a, b):
    """(시드, 보내는 사람, 받는 사람)마다 고정된 0~1 난수 (splitmix64).
    추가 홍보를 넣어도 같은 연결의 성공 여부는 그대로라, 비교할 때 운 차이가 섞이지 않는다"""
    x = (seed * 0x9E3779B97F4A7C15 + a * 0xBF58476D1CE4E5B9 + b * 0x94D049BB133111EB) & 0xFFFFFFFFFFFFFFFF
    x ^= x >> 30
    x = (x * 0xBF58476D1CE4E5B9) & 0xFFFFFFFFFFFFFFFF
    x ^= x >> 27
    x = (x * 0x94D049BB133111EB) & 0xFFFFFFFFFFFFFFFF
    x ^= x >> 31
    return x / 2 ** 64


def spread(people, adj, seeds, share, interest, w, seed, by_id, extra_at=None, ctx=None, seed_channel=None, promo=None):
    """한 주에 steps_per_week 단계. 소식이 닿는 경로는 세 가지.
    - 입소문: 새로 들은 사람은 연결된 사람마다 한 번씩 전할 기회 (경로 = 관계망 type)
    - 검색: 아직 모르는 사람이 매 단계 검색으로 발견. 집단의 검색 성향 x 관심도 x 지금까지 쌓인 후기 수
    - 지나가다: 가게 근처에 살거나 낮에 그 동을 지나는 사람이 매 단계 가게를 직접 발견. 집단의 '지나가다' 성향 x 영업시간 유동인구
    extra_at={단계: [id,...]} 이면 그 단계에 추가 홍보로 소식을 직접 들은 사람을 더함.
    promo (홍보 시나리오, 선택): {"pre": [(id, 경로)], "pre_steps": n, "at": {단계: [(id, 경로)]},
                                  "share": {id: 전달 의향}, "reviews": {단계: 추가 후기}}
      - pre: 오픈 전에 미리 알게 된 사람. pre_steps 단계 동안 입소문만 돌고(가게가 아직 없으니 검색·지나가다 없음) 0주차로 기록
      - at: 그 단계에 홍보로 직접 알게 된 사람, share: 허브 쿠폰처럼 전달 의향이 올라간 사람, reviews: 체험단 후기
    반환: steps, heard_week{id: 주차}, fails, channel{id: 경로}, parent{id: 전해준 사람}"""
    ctx = ctx or {}
    promo = promo or {}
    if promo.get("share"):
        share = {**share, **promo["share"]}
    informed, heard_week, channel, parent = set(), {}, {}, {}
    fails = Counter()
    scale = w.get("transmission_scale", 1.0)

    # 오픈 전 홍보: 입소문만 미리 돈다
    pre = [(i, ch) for i, ch in promo.get("pre", []) if i not in informed]
    frontier = []
    for i, ch in pre:
        informed.add(i); heard_week[i] = 0; channel[i] = ch
        frontier.append(i)
    for _ in range(promo.get("pre_steps", 0) if pre else 0):
        new = []
        for s in sorted(frontier):
            for r, path in sorted(adj[s]):
                # 오픈 전엔 가게를 직접 겪은 사람이 없어서 전달력이 약함 (pre_share_factor)
                if r not in informed and coin(seed, s, r) < (scale * share[s] * interest[r] * w["path_weight"][path]
                                                             * w.get("promo", {}).get("pre_share_factor", 1.0)):
                    informed.add(r); heard_week[r] = 0; channel[r] = path; parent[r] = s
                    new.append(r)
        frontier = new

    # 오픈: 원래 출발점 + 오픈 전에 알게 된 사람 중 아직 안 전한 사람
    for i in seeds:
        if i not in informed:
            informed.add(i); heard_week[i] = 0; channel[i] = (seed_channel or {}).get(i, "지나가다")
    frontier = sorted(set(frontier) | set(seeds))
    steps = [sorted(informed)]
    extra_at = {k: [x if isinstance(x, tuple) else (x, "추가홍보") for x in v] for k, v in (extra_at or {}).items()}
    for k, v in promo.get("at", {}).items():
        extra_at.setdefault(k, []).extend(v)
    review_extra = 0.0
    spw = w.get("steps_per_week", 1)
    max_steps = w["horizon_weeks"] * spw
    d = w["interest_decay"]
    vprob = ctx.get("visit_prob", {})
    search_p = ctx.get("search_p", {})   # 사람별 '후기가 충분할 때' 단계당 검색 발견 확률
    walk_p = ctx.get("walk_p", {})       # 사람별 단계당 지나가다 발견 확률
    candidates = sorted(i for i in by_id if search_p.get(i, 0) > 0 or walk_p.get(i, 0) > 0)
    while len(steps) < max_steps:
        step = len(steps)
        new = []
        for s in frontier:
            for r, path in sorted(adj[s]):
                if r in informed:
                    continue
                if coin(seed, s, r) < scale * share[s] * interest[r] * w["path_weight"][path]:
                    informed.add(r)
                    new.append(r)
                    channel[r] = path
                    parent[r] = s
                else:
                    fails[(by_id[r]["group"], path)] += 1
        if candidates:
            # 지금까지 쌓인 후기 (모델 단위): 소식 들은 사람들의 누적 첫 방문 기댓값 x 후기 작성률
            week = step / spw
            visits = sum(vprob.get(i, 0) * (1 - d ** max(week - h, 0)) for i, h in heard_week.items())
            review_extra += promo.get("reviews", {}).get(step, 0.0)
            reviews = w.get("review_rate", 0) * visits + review_extra
            review_f = reviews / (reviews + w.get("review_half", 1)) if reviews > 0 else 0.0
            for i in candidates:
                if i in informed:
                    continue
                p_walk = walk_p.get(i, 0)
                p_search = search_p.get(i, 0) * review_f
                u = coin(seed, i, 10_000_000 + step)
                if u < p_walk:
                    ch = "지나가다"
                elif u < p_walk + p_search:
                    ch = "검색"
                else:
                    continue
                informed.add(i)
                new.append(i)
                channel[i] = ch
        for r, ch in extra_at.get(step, []):
            if r not in informed:
                informed.add(r)
                new.append(r)
                channel[r] = ch
        if not new and not candidates and not any(k > step for k in extra_at):
            break
        new = sorted(set(new))
        for r in new:
            heard_week[r] = step // spw
        steps.append(new)
        frontier = new
    return steps, heard_week, fails, channel, parent


def find_hubs(heard_week, parent, people_by_id, reactions, top):
    """입소문 허브: 그 사람을 거쳐(자손 포함) 소식을 들은 인원(reach)이 많은 시민"""
    children = defaultdict(list)
    for c, p in parent.items():
        children[p].append(c)
    reach = {}
    for i in sorted(heard_week, key=lambda i: -heard_week[i]):  # 늦게 들은 사람부터 올라가며 합산
        reach[i] = sum(1 + reach.get(c, 0) for c in children.get(i, []))
    ranked = sorted((i for i in reach if reach[i] > 0), key=lambda i: (-reach[i], i))[:top]
    out = []
    for i in ranked:
        p = people_by_id[i]
        out.append({"id": i, "group": p["group"], "dong": p["dong"], "reach": reach[i],
                    "direct": len(children.get(i, [])), "week": heard_week[i],
                    "community": [c for c in reactions.get(p["group"], {}).get("community", []) if c != "없음"]})
    return out


def weekly_visits(heard_week, visit_prob, revisit, w, boost=None):
    """주별 방문 (모델 단위, 기댓값)
    - 첫 방문: 소식을 들은 주부터 관심이 매주 interest_decay 비율로 식으며 나눠서 방문 (합 = visit_prob)
    - 재방문: 첫 방문한 사람이 이후 매주 revisit 확률로 다시 오고, 그 확률도 revisit_retention 비율로 줄어듦
    - boost=(주차 집합, {id: 배수}): 오픈 할인 기간에 첫 방문이 그 배수만큼 늘어남"""
    b_weeks, b_mult = boost if boost else (set(), {})
    weeks = w["horizon_weeks"]
    d = w["interest_decay"]
    first = [0.0] * weeks
    repeat = [0.0] * weeks
    for i, h in heard_week.items():
        v = visit_prob.get(i, 0.0)
        if v <= 0 or h >= weeks:
            continue
        r0 = revisit.get(i, 0.0)
        for t in range(h, weeks):
            f = v * (1 - d) * d ** (t - h)
            if t in b_weeks:
                f *= b_mult.get(i, 1.0)
            first[t] += f
            for u in range(t + 1, weeks):  # 그 주에 처음 온 사람들의 이후 재방문
                repeat[u] += f * r0 * w["revisit_retention"] ** (u - t - 1)
    return [a + b for a, b in zip(first, repeat)], first


def breakeven(weekly_real, price, rent, w):
    """주간 공헌이익(매출 × 마진율) >= 주간 고정비(월세 + 기타 고정비)가 되는 첫 주 (1주차부터 셈)"""
    if not price or not rent:
        return None
    fixed_week = (rent + w.get("other_fixed_monthly", 0)) / 4.345
    for t, n in enumerate(weekly_real):
        if n * price * w["gross_margin"] >= fixed_week:
            return t + 1
    return None


def stall_week(heard_week, w):
    """주별 새로 듣는 사람 수가 정점 이후 정점의 repromo_trigger 비율 아래로 처음 떨어지는 주.
    12주 안에 안 떨어지면 정점 다음 주 (그래도 끝까지 가면 horizon의 절반)"""
    weeks = w["horizon_weeks"]
    counts = [0] * weeks
    for h in heard_week.values():
        if 0 < h < weeks:
            counts[h] += 1
    peak = max(range(1, weeks), key=lambda i: (counts[i], -i))
    for i in range(peak + 1, weeks):
        if counts[i] < w["repromo_trigger"] * counts[peak]:
            return i
    return min(peak + 1, weeks - 2) if counts[peak] else weeks // 2


def simulate(dong_code, dong, people, edges, reactions, geo, w, seed, stats=None, scenario=None, runs=1):
    by_id = {p["id"]: p for p in people}
    st = (stats or {}).get(dong_code)
    sc = scenario or {}

    # 반응 없는 집단은 무관심(0) -> 결과가 낙관적으로 안 나오게
    interest = {p["id"]: to01(reactions[p["group"]]["interest"]) if p["group"] in reactions else 0.0 for p in people}
    share = {p["id"]: to01(reactions[p["group"]]["share"]) if p["group"] in reactions else 0.0 for p in people}
    revisit = {p["id"]: to01(reactions[p["group"]].get("revisit", w["revisit_default"])) * w["revisit_rate_max"]
               if p["group"] in reactions else 0.0 for p in people}

    adj = defaultdict(list)
    for e in edges:
        adj[e["from"]].append((e["to"], e["type"]))
        adj[e["to"]].append((e["from"], e["type"]))

    slat, slng = store_point(dong_code, people, geo)
    dist = {p["id"]: distance_m(p["lat"], p["lng"], slat, slng) for p in people}
    local = sorted((p["id"] for p in people if p["dong_code"] == dong_code), key=lambda i: (dist[i], i))
    base_seeds = local[: w["seed_count"]] or sorted(dist, key=lambda i: (dist[i], i))[: w["seed_count"]]
    outsiders = sorted(p["id"] for p in people if p["dong_code"] != dong_code)

    # 영업시간·요일 (입력이 없으면 영향 없음)
    f_float, f_work = hours_factor(st, sc.get("open_hours"), w)
    f_days, weekend_share = days_factor(st, sc.get("open_days"))

    # 낮 방문 비율·경쟁 계수 (동 단위, 시드와 무관)
    n_day, p_day, comp = 0, 0.0, 1.0
    if st:
        n_day = min(int(round(w.get("day_seeds_per_million_floating", 0) * st["floating_pop"] * f_float / 1e6
                              + w.get("day_seeds_per_1000_workers", 0) * st["worker_pop"] * f_work / 1000)),
                    w.get("day_seed_max", 0))
        p_day = min(w.get("day_visit_cap", 0.0),
                    w.get("day_visit_per_million_floating", 0) * st["floating_pop"] * f_float / 1e6
                    + w.get("day_visit_per_1000_workers", 0) * st["worker_pop"] * f_work / 1000)
        k = w.get("competition_k", 0)
        if k:
            comp = (k / (k + st["store_count"])) ** w.get("competition_alpha", 1.0)

    # 연령대: 그 동을 지나는 사람의 나이 구성 / 반경 안 시민의 나이 구성 -> 나이대별 배수
    age_of = {p["id"]: p["group"].split("_")[0] for p in people}
    age_w = {a: 1.0 for a in FLOW_AGE}
    flow = (st or {}).get("flow") or {}
    if all(flow.get(c) for c in FLOW_AGE.values()):
        tot_f = sum(flow[c] for c in FLOW_AGE.values())
        cnt = Counter(age_of[i] for i in outsiders) or Counter(age_of.values())
        tot_p = sum(cnt.values())
        for a, c in FLOW_AGE.items():
            if cnt.get(a):
                age_w[a] = (flow[c] / tot_f) / (cnt[a] / tot_p)

    # 사람별 방문 확률 (경쟁 반영 전 수요 기준). 낮 방문 비율은 나이대 배수만큼 달라짐
    scale_m = w["visit_distance_scale_m"]
    demand_prob = {}
    for p in people:
        i = p["id"]
        if interest[i] >= w["visit_interest_min"]:
            pd_i = min(w.get("day_visit_cap", 0.0), p_day * age_w.get(age_of[i], 1.0))
            demand_prob[i] = interest[i] * ((1 - pd_i) * math.exp(-dist[i] / scale_m) + pd_i)
    visit_prob = {i: v * comp for i, v in demand_prob.items()}

    # 검색·지나가다 발견 확률 (사람별, 단계당). 반응 파일에 discovery가 없으면 기본 비율
    def disc(g, key):
        return reactions.get(g, {}).get("discovery", {}).get(key, w["discovery_default"])
    walk_scale = w["walk_distance_scale_m"]
    search_p, walk_p = {}, {}
    for p in people:
        i = p["id"]
        if interest[i] <= 0:
            continue
        search_p[i] = w["search_base"] * disc(p["group"], "네이버검색") * interest[i]
        pd_i = min(w.get("day_visit_cap", 0.0), p_day * age_w.get(age_of[i], 1.0))
        near = math.exp(-dist[i] / walk_scale) + pd_i * f_float
        walk_p[i] = w["walk_base"] * disc(p["group"], "지나가다") * near
    ctx = {"visit_prob": visit_prob, "search_p": search_p, "walk_p": walk_p}

    # 가격·월세 (입력 없으면 동 평균 객단가, 월세는 입력값만)
    price = sc.get("avg_price") or (st or {}).get("avg_price") or w["default_avg_price"]
    rent = sc.get("rent")
    cscale = w.get("customer_scale", 1.0) * f_days

    def pick_day_seeds(s):
        """낮 출발점: 다른 동 주민 중 그 동을 지나는 나이 구성에 맞춰 가중 추출"""
        if not n_day:
            return []
        rng = random.Random(s)
        pool = list(outsiders)
        chosen = []
        for _ in range(min(n_day, len(pool))):
            weights = [age_w.get(age_of[i], 1.0) for i in pool]
            j = rng.choices(range(len(pool)), weights=weights)[0]
            chosen.append(pool.pop(j))
        return sorted(chosen)

    # 시드 여러 개 실행 -> 평균과 범위, 애니메이션은 중앙값에 가장 가까운 실행
    rows = []
    for k_run in range(runs):
        s = seed + k_run
        day_seeds = pick_day_seeds(s)
        seeds = sorted(set(base_seeds) | set(day_seeds))
        steps, heard_week, fails, channel, parent = spread(people, adj, seeds, share, interest, w, s, by_id, ctx=ctx)
        demand = sum(demand_prob.get(i, 0) for i in heard_week)
        weekly_model, _ = weekly_visits(heard_week, visit_prob, revisit, w)
        rows.append({"seed": s, "seeds": seeds, "steps": steps, "heard_week": heard_week, "fails": fails,
                     "channel": channel, "parent": parent,
                     "demand": demand, "visitors": demand * comp, "weekly_model": weekly_model,
                     "day_seeds": len(day_seeds)})
    vis = sorted(r["visitors"] for r in rows)
    median = vis[len(vis) // 2]
    main = min(rows, key=lambda r: (abs(r["visitors"] - median), r["seed"]))
    H = w["horizon_weeks"]
    weekly_model = [sum(r["weekly_model"][t] for r in rows) / runs for t in range(H)]
    weekly_real = [int(round(x * cscale)) for x in weekly_model]

    # 추가 홍보: 소식이 멈추는 주(실행들의 중앙값)에 관심 있는 미인지 시민 repromo_count명에게 다시 알림.
    # 실행마다 같은 난수로 '했을 때 / 안 했을 때'를 비교해 평균 (추가 홍보가 손해로 나오지 않음)
    spw = w.get("steps_per_week", 1)
    rw = sorted(stall_week(r["heard_week"], w) for r in rows)[runs // 2]
    with_model = [0.0] * H
    for r in rows:
        not_yet = [i for i in by_id if i not in r["heard_week"] or r["heard_week"][i] >= rw]
        pick = sorted((i for i in not_yet if interest[i] >= w["visit_interest_min"]),
                      key=lambda i: (dist[i], i))[: w["repromo_count"]]
        _, heard_with, _, _, _ = spread(people, adj, r["seeds"], share, interest, w, r["seed"], by_id,
                                        extra_at={rw * spw: pick}, ctx=ctx)
        wm, _ = weekly_visits(heard_with, visit_prob, revisit, w)
        with_model = [a + b / runs for a, b in zip(with_model, wm)]
    with_real = [int(round(x * cscale)) for x in with_model]
    without_real = weekly_real

    # 확산이 멈춘 집단: 인지율 10% 미만 + 가장 많이 실패한 경로 (대표 실행 기준)
    total, heard = Counter(), Counter()
    for p in people:
        total[p["group"]] += 1
        heard[p["group"]] += p["id"] in main["heard_week"]
    stopped_at = []
    for g in sorted(total):
        if heard[g] / total[g] < w["stop_awareness_threshold"]:
            tried = [(n, path) for (grp, path), n in main["fails"].items() if grp == g]
            stopped_at.append({"group": g, "path": max(tried)[1] if tried else "동네"})

    # 채널별 유입: 모든 실행을 합쳐 집단별로 어떤 경로로 들었는지 비율
    ch_cnt = defaultdict(Counter)
    for r in rows:
        for i, ch in r["channel"].items():
            ch_cnt[by_id[i]["group"]][ch] += 1
    channel_share = {g: {ch: round(n / sum(c.values()), 3) for ch, n in c.most_common()}
                     for g, c in sorted(ch_cnt.items())}
    tot = Counter()
    for c in ch_cnt.values():
        tot.update(c)
    channel_total = {ch: round(n / sum(tot.values()), 3) for ch, n in tot.most_common()}
    hubs = find_hubs(main["heard_week"], main["parent"], by_id, reactions, w["hub_top"])

    # 집단별 인지율: 반경 안 그 집단 시민 중 12주 안에 소식을 들은 비율 (모든 실행 평균)
    g_total = Counter(p["group"] for p in people)
    group_awareness = {}
    for g in sorted(g_total):
        ids = [p["id"] for p in people if p["group"] == g]
        group_awareness[g] = round(sum(sum(i in r["heard_week"] for i in ids) for r in rows) / (len(ids) * runs), 3)

    # 홍보 시나리오·예산 배분 (후보 동 모드에서만)
    promo_out = {}
    if sc.get("_promo", False):
        import promo as promo_mod
        model = {"people": people, "by_id": by_id, "adj": adj, "share": share, "interest": interest,
                 "w": w, "ctx": ctx, "visit_prob": visit_prob, "revisit": revisit, "rows": rows,
                 "main": main, "reactions": reactions, "dist": dist, "stall_week": rw,
                 "price": price, "rent": rent, "cscale": cscale, "baseline_real": weekly_real,
                 "budget": sc.get("budget") or w["promo"]["default_budget"], "spread": spread,
                 "weekly_visits": weekly_visits, "breakeven": breakeven, "find_hubs": find_hubs,
                 "stopped_at": None, "dong_code": dong_code}
        promo_out = promo_mod.plan(model)

    mean = lambda xs: sum(xs) / len(xs)
    return {"dong_code": dong_code, "dong": dong, "store_location": [round(slat, 6), round(slng, 6)],
            "seed": main["seed"], "steps": main["steps"],
            "visitors": int(round(mean([r["visitors"] for r in rows]))),
            "stopped_at": stopped_at,
            "visitors_demand": int(round(mean([r["demand"] for r in rows]))),
            "visitors_range": [int(round(vis[0])), int(round(vis[-1]))],
            "runs": runs,
            "competition_factor": round(comp, 3),
            "daytime_share": round(p_day, 3),
            "day_seeds": main["day_seeds"],
            "population_in_radius": len(people),
            "is_estimated": (st or {}).get("is_estimated", 0),
            # 시간 축 (실제 손님 수로 환산: customer_scale)
            "weekly_visitors": weekly_real,
            "weekly_visits_model": [round(x, 3) for x in weekly_model],
            "monthly_customers": sum(weekly_real[-4:]),
            "breakeven_week": breakeven(weekly_real, price, rent, w),
            "repromo": {"week": rw, "count": w["repromo_count"], "with": with_real, "without": without_real,
                        "gain": sum(with_real) - sum(without_real)},
            "open_hours": sc.get("open_hours"), "open_days": sc.get("open_days"),
            "hours_factor": round(f_float, 3), "days_factor": round(f_days, 3),
            "weekend_share": round(weekend_share, 3) if weekend_share is not None else None,
            "day_age_mix": {a: round(v, 2) for a, v in age_w.items()},
            "assumptions": {"avg_price": int(price), "rent": rent, "gross_margin": w["gross_margin"],
                            "other_fixed_monthly": w.get("other_fixed_monthly", 0),
                            "customer_scale": w.get("customer_scale", 1.0)},
            "channel_share": channel_share, "channel_total": channel_total, "hubs": hubs,
            "group_awareness": group_awareness,
            "group_count": dict(sorted(g_total.items())),  # 반경 안 집단별 가상 시민 수 (인원 적은 집단 거르기용)
            "scenarios": promo_out.get("scenarios"), "budget_plan": promo_out.get("budget_plan"),
            "hub_vs_broad": promo_out.get("hub_vs_broad"), "prescriptions": promo_out.get("prescriptions"),
            "diagnosis": None}


# ---------- 실행 ----------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="frontend/data/real")
    ap.add_argument("--weights", default="config/weights.json")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--all-dongs", action="store_true", help="페르소나에 있는 모든 동 계산 (검증용)")
    ap.add_argument("--dongs", nargs="*", help="계산할 동 코드 목록")
    ap.add_argument("--out", default=None, help="결과 저장 폴더 (기본: --data와 같은 폴더)")
    ap.add_argument("--no-promo", action="store_true", help="홍보 시나리오·예산 계산 건너뛰기")
    ap.add_argument("--runs", type=int, default=None, help="시드 몇 개를 돌려 평균낼지 (기본: 후보 5, 전체 동 1)")
    args = ap.parse_args()

    folder = ROOT / args.data
    w = load_json(ROOT / args.weights)
    people = load_personas(folder)
    reactions = load_reactions(folder)
    stats = load_dong_stats(folder)
    geo = load_json(folder / "dongs.geojson") if (folder / "dongs.geojson").exists() else None
    scenario = load_json(folder / "scenario.json") if (folder / "scenario.json").exists() else {}
    scenario["_promo"] = not args.all_dongs and not args.no_promo
    for code, ll in (scenario.get("store_locations") or {}).items():
        if ll and len(ll) == 2:
            STORE_OVERRIDE[str(code)] = (float(ll[0]), float(ll[1]))
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import promo as _promo_mod
    _promo_mod.AD_COSTS = load_json(folder / "ad_costs.json") if (folder / "ad_costs.json").exists() else []
    runs = args.runs or (1 if args.all_dongs else w.get("runs", 5))
    out_dir = ROOT / args.out if args.out else folder
    out_dir.mkdir(parents=True, exist_ok=True)

    missing = sorted({p["group"] for p in people} - set(reactions))
    if missing:
        print(f"[경고] reactions.json에 없는 집단 {len(missing)}개 -> 무관심(0)으로 처리: {missing[:5]}")
    print(f"시민 {len(people)}명, 동별 통계 {'있음 (' + str(len(stats)) + '개 동)' if stats else '없음 (유동인구·경쟁 미반영)'}, "
          f"가중치 {w.get('version')}")

    names = {}
    for p in people:
        names.setdefault(p["dong_code"], p["dong"])
    if args.all_dongs:
        dongs = dict(sorted(names.items()))
    elif args.dongs:
        dongs = {c: names.get(c, c) for c in args.dongs}
    else:
        dongs = DEFAULT_CANDIDATES

    radius = w.get("sim_radius_m")
    all_edges = set()
    for code, name in dongs.items():
        sub = local_people(code, people, geo, radius) if radius else people
        edges = build_neighbor_edges(sub, w["neighbor_radius_m"], w["neighbor_max"])
        edges += build_community_edges(sub, reactions, w["community_links_per_person"],
                                       w.get("community_radius_m", {}), f"{args.seed}-{code}")
        res = simulate(code, name, sub, edges, reactions, geo, w, args.seed, stats, scenario, runs)
        with open(out_dir / f"result_{code}.json", "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=2)
        if not args.all_dongs:
            all_edges.update((e["from"], e["to"], e["type"]) for e in edges)
        heard = sum(len(s) for s in res["steps"])
        print(f"{name}: 반경 내 {len(sub)}명 중 {heard}명 인지({heard / max(len(sub), 1):.0%}, 12주) / "
              f"방문 가능 {res['visitors']}명 (범위 {res['visitors_range'][0]}~{res['visitors_range'][1]}, 수요 {res['visitors_demand']}) / "
              f"3달째 월 손님 {res['monthly_customers']}명 / 손익분기 {res['breakeven_week'] or '-'}주차 / "
              f"{res['repromo']['week']}주차 추가 홍보 시 +{res['repromo']['gain']}명 / 멈춘 집단 {len(res['stopped_at'])}개")
        bp = res.get("budget_plan")
        if bp:
            hv = res["hub_vs_broad"]
            print(f"   홍보 {bp['budget']:,}원 추천: {bp['split']} {bp['timing']}{' + 오픈 할인' if bp['open_discount'] else ''} "
                  f"-> 12주 +{bp['visitors_gain']}명 (만 원당 {bp['per_10k']}명, 광고비의 {bp['roas']}배 매출, {bp['payback_week'] or '-'}주 만에 회수, "
                  f"손익분기 {bp['breakeven_without'] or '-'} -> {bp['breakeven_with'] or '-'}주차, "
                  f"단가 바뀌어도 1순위 유지 {int(bp['robust']['same_top_share'] * 100)}%) / "
                  f"허브 1명은 평균 {hv['reach_hub']}명, 인스타로 안 사람은 {hv['reach_broad']}명에게 퍼뜨림 ({hv['per_person_ratio']}배)")

    if all_edges:
        with open(out_dir / "edges.json", "w", encoding="utf-8") as f:
            json.dump([{"from": a, "to": b, "type": t} for a, b, t in sorted(all_edges)], f, ensure_ascii=False)


if __name__ == "__main__":
    main()
