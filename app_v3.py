"""노른자 v3 화면 (원래 app.py와 별개)

실행:  streamlit run app_v3.py
- 필수 입력: 가게, 후보 동네(서울 426개 동에서 검색, 최대 3곳)
- 선택 입력: 영업시간, 영업 요일, 월세, 객단가, 홍보 예산. 넣을수록 결과가 자세해진다
  (월세 -> 손익분기, 홍보 예산 -> 홍보 추천, 영업시간·요일 -> 시간대·요일 반영)
- 미리 계산된 결과가 있으면 그대로 쓰고, 없거나 조건이 다르면 엔진을 바로 돌린다 (runs/ 폴더에 저장)
- 페이지: ?page=home(첫 화면), result(결과), how(계산 방법). 개발용 정보는 ?debug=1
"""
import csv
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

try:  # 지도에서 가게 자리 찍기 (requirements.txt에 streamlit-folium, folium 추가)
    import folium
    from streamlit_folium import st_folium
except ImportError:
    folium = None

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "frontend" / "data" / "real"
VIEW = ROOT / "frontend" / "v3" / "view.html"
RUNS = ROOT / "runs"
RADIUS_KM = 3.0
DAYS = ["월", "화", "수", "목", "금", "토", "일"]
ENGINE_FILES = ["personas.csv", "reactions.json", "dong_stats.csv", "dongs.geojson", "ad_costs.json"]

st.set_page_config(page_title="노른자", layout="wide", initial_sidebar_state="collapsed")
page = st.query_params.get("page", "home")

# ---------- 화면 기본 스타일 ----------
st.markdown("""
<style>
@import url('https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.min.css');
html, body, [class*="css"], .stApp, input, textarea, button { font-family: 'Pretendard', sans-serif !important; }
.stApp { background: #FAFAF7; color: #111111; }
header[data-testid="stHeader"], footer, #MainMenu, [data-testid="stSidebar"], [data-testid="collapsedControl"],
[data-testid="stToolbar"], [data-testid="stDecoration"], [data-testid="stStatusWidget"] { display: none !important; }
.block-container { padding: 0 0 48px 0 !important; max-width: 100% !important; }
.nr-top { background: #111111; height: 64px; display: flex; align-items: center; justify-content: space-between; padding: 0 40px; }
.nr-top a { color: #FFFFFF !important; text-decoration: none !important; }
.nr-logo { display: flex; align-items: center; gap: 10px; font-size: 18px; font-weight: 900; }
.nr-nav { display: flex; gap: 32px; font-size: 14px; }
.nr-nav a:hover, .nr-logo:hover { color: #F5B301 !important; }
.nr-search-title { width: calc(100% - 80px); max-width: 1280px; margin: 8px auto 0; font-size: 24px; font-weight: 900; }
.nr-hero .eyebrow { font-size: 14px; font-weight: 600; display: flex; align-items: center; gap: 8px; }
.nr-hero .eyebrow i { width: 8px; height: 8px; border-radius: 999px; background: #E9A400; display: inline-block; }
.nr-hero h1 { font-size: 64px; line-height: 1.1; font-weight: 900; letter-spacing: -0.03em; margin: 14px 0 12px; color: #111111; }
.nr-hero p { font-size: 18px; line-height: 1.6; color: #111111; margin: 0; }
/* 입력 영역 */
.st-key-nr-search { box-sizing: border-box; width: calc(100% - 80px); max-width: 1280px; margin: 8px auto 0 !important;
  background: #FFFFFF; border: 3px solid #111111 !important; border-radius: 28px !important; padding: 32px 36px 28px !important;
  box-shadow: 10px 10px 0 #F5B301; }
.st-key-nr-search div[data-testid="stVerticalBlockBorderWrapper"] { border: none !important; }
.stTextInput input, div[data-baseweb="select"] input, div[data-baseweb="select"] div, .stNumberInput input {
  font-family: 'Pretendard', sans-serif !important; font-size: 17px !important; font-weight: 400 !important; color: #111111; }
.stTextInput input { min-height: 56px; border-radius: 0 !important; }
.stTextInput input::placeholder { color: #8A8A8A !important; opacity: 1 !important; }
div[data-baseweb="select"] [aria-hidden="true"], div[data-baseweb="select"] input::placeholder { color: #8A8A8A !important; }
div[data-baseweb="input"], div[data-baseweb="select"] > div { border-radius: 0 !important; border-color: #111111 !important; }
div[data-baseweb="select"] > div { min-height: 56px; }
.stTextInput label p, .stMultiSelect label p, .stNumberInput label p, .stSelectbox label p, .stCheckbox label p {
  font-size: 15px !important; font-weight: 600 !important; color: #111111 !important; }
span[data-baseweb="tag"], .stMultiSelect [data-baseweb="tag"] { background-color: #111111 !important; border-radius: 0 !important; }
span[data-baseweb="tag"] span, span[data-baseweb="tag"] svg { color: #FFFFFF !important; fill: #FFFFFF !important; }
div[data-testid="stExpander"] details { border-radius: 0 !important; border: 1px solid #E0E0E0 !important; }
.stButton button { border-radius: 999px; height: 48px; padding: 0 28px; transition: background-color 200ms ease, color 200ms ease; }
.stButton button p { font-family: 'Pretendard', sans-serif !important; font-weight: 600 !important; font-size: 16px !important; }
.stButton button[kind="primary"], [data-testid="stBaseButton-primary"] { background: #F5B301 !important; color: #111111 !important; border: none !important; }
.stButton button[kind="primary"]:hover, [data-testid="stBaseButton-primary"]:hover { background: #E5A500 !important; }
.stButton button[kind="secondary"], [data-testid="stBaseButton-secondary"] { background: #FFFFFF !important; color: #111111 !important; border: 1.5px solid #111111 !important; }
.stButton button[kind="secondary"]:hover, [data-testid="stBaseButton-secondary"]:hover { background: #111111 !important; color: #FFFFFF !important; }
.stButton button[kind="secondary"]:disabled, [data-testid="stBaseButton-secondary"]:disabled { border-color: #CFCFCF !important; color: #9A9A9A !important; background: #FFFFFF !important; }
.stButton button[kind="tertiary"], [data-testid="stBaseButton-tertiary"] { height: auto; padding: 4px 0; background: none !important; border: none !important; }
.stButton button[kind="tertiary"] p, [data-testid="stBaseButton-tertiary"] p { font-size: 14px !important; color: #595959 !important; text-decoration: underline; }
[data-testid="stBaseButton-pills"] { border-radius: 999px !important; border: 1.5px dashed #BDBDBD !important; background: #FFFFFF !important; min-width: 56px; height: 48px; }
[data-testid="stBaseButton-pills"] p { color: #9A9A9A !important; }
[data-testid="stBaseButton-pillsActive"] { border-radius: 999px !important; border: 1.5px solid #111111 !important; background: #111111 !important; min-width: 56px; height: 48px; }
.nr-days { display: flex; flex-wrap: wrap; gap: 8px 20px; font-size: 15px; margin: 6px 0 0; }
.nr-days .on { color: #111111; font-weight: 700; } .nr-days .off { color: #9A9A9A; }
[data-testid="stBaseButton-pillsActive"] p { color: #FFFFFF !important; }
.nr-track { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 18px; padding-bottom: 18px; margin-bottom: 8px; border-bottom: 1px solid #E0E0E0; }
.nr-step { display: inline-flex; align-items: center; gap: 8px; font-size: 14px; color: #9A9A9A; }
.nr-step b { width: 24px; height: 24px; border-radius: 999px; display: inline-flex; align-items: center; justify-content: center; font-size: 12px; border: 1.5px solid #CFCFCF; }
.nr-step.opt b { border-style: dashed; }
.nr-step.done { color: #111111; } .nr-step.done b { background: #111111; border: 1.5px solid #111111; color: #FFFFFF; }
.nr-step.now { color: #111111; font-weight: 700; } .nr-step.now b { background: #F5B301; border: 1.5px solid #F5B301; color: #111111; }
.nr-sep { font-size: 12px; color: #595959; padding-left: 14px; border-left: 1.5px dashed #CFCFCF; }
.nr-sum-label { font-size: 14px; color: #595959; }
.nr-sum-val { font-size: 16px; color: #111111; }
.nr-q { display: flex; align-items: center; gap: 10px; font-size: 22px; font-weight: 700; margin: 18px 0 10px; }
.nr-badge { font-size: 12px; font-weight: 700; padding: 3px 10px; border-radius: 999px; }
.nr-badge.req { background: #111111; color: #FFFFFF; }
.nr-badge.opt { background: #FFFFFF; color: #595959; border: 1px dashed #9A9A9A; }
.nr-ok { font-size: 14px; color: #7A5200; background: #FFF6DA; padding: 8px 14px; margin: 12px 0 0; display: inline-block; }
.st-key-nr-bar { box-sizing: border-box; width: calc(100% - 80px); max-width: 1280px; margin: 16px auto 0 !important; }
.stButton button:focus-visible { outline: 2px solid #111111; outline-offset: 2px; }
.nr-note { font-size: 14px; color: #595959; margin: 4px 0 0; }
/* 안내·오류 메시지도 검색 상자와 같은 폭으로 */
div[data-testid="stAlert"], div[data-testid="stNotification"] { box-sizing: border-box; width: calc(100% - 80px) !important;
  max-width: 1280px; margin: 12px auto 0 !important; }
</style>
<div class="nr-top">
  <a class="nr-logo" href="?page=home" target="_self">
    <svg width="22" height="22" viewBox="0 0 22 22" aria-hidden="true"><circle cx="11" cy="11" r="10" fill="#FFFFFF"/><circle cx="11" cy="11" r="5" fill="#F5B301"/></svg>
    노른자
  </a>
  <nav class="nr-nav"><a href="?page=how" target="_self">계산 방법</a></nav>
</div>
""", unsafe_allow_html=True)


# ---------- 데이터 ----------
def read_json(path, default=None):
    try:
        return json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        return default


@st.cache_data
def load_dongs():
    """서울 426개 동: 화면 표시 이름 -> 동 코드 (같은 이름 동이 여러 구에 있어서 구 이름을 붙임)"""
    out = {}
    with open(DATA / "dong_stats.csv", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            label = f"{row['dong']} · {row.get('gu') or ''}".strip(" ·")
            out[label] = row["dong_code"]
    return dict(sorted(out.items()))


@st.cache_data
def load_people():
    people = {}
    with open(DATA / "personas.csv", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            people[int(row["id"])] = (float(row["lat"]), float(row["lng"]), row["dong_code"])
    return people


@st.cache_data
def default_store_point(code):
    """엔진이 쓰는 기본 가게 자리 (경계 파일 꼭짓점 평균, 없으면 그 동 주민 평균)"""
    geo = read_json(DATA / "dongs.geojson")
    if geo:
        for feat in geo.get("features", []):
            if str(feat["properties"].get("adm_cd2")) == code:
                coords = feat["geometry"]["coordinates"]
                ring = coords[0][0] if feat["geometry"]["type"] == "MultiPolygon" else coords[0]
                return (sum(c[1] for c in ring) / len(ring), sum(c[0] for c in ring) / len(ring))
    mine = [v for v in load_people().values() if v[2] == code]
    return (sum(p[0] for p in mine) / len(mine), sum(p[1] for p in mine) / len(mine)) if mine else (37.55, 126.92)


def km(a, b):
    dy = (a[0] - b[0]) * 111.0
    dx = (a[1] - b[1]) * 111.0 * math.cos(math.radians(a[0]))
    return math.hypot(dx, dy)


def diag_summary(text):
    if not text:
        return None
    if "[요약]" in text:
        return text.split("[요약]", 1)[1].split("[", 1)[0].strip()
    return text.strip().split("\n")[0][:200]


def build_dong(r, steps_per_week):
    people = load_people()
    code = r["dong_code"]
    center = tuple(r["store_location"]) if r.get("store_location") else default_store_point(code)
    heard = {}
    for step, ids in enumerate(r.get("steps", [])):
        for i in ids:
            heard.setdefault(int(i), step // steps_per_week)
    dots = [[round(lat, 5), round(lng, 5), heard.get(pid, -1)] for pid, (lat, lng, _) in people.items()
            if pid in heard or km((lat, lng), center) <= RADIUS_KM]
    calib = read_json(DATA / f"calibration_demo_{code}.json")
    rev = read_json(DATA / f"revision_{code}.json") or {}
    bp = r.get("budget_plan") or {}
    hv = r.get("hub_vs_broad") or {}
    return {
        "code": code, "dong": r["dong"], "center": center, "dots": dots,
        "monthly": r.get("monthly_customers"), "breakeven": r.get("breakeven_week"),
        "weekly": r.get("weekly_visitors"), "range": r.get("visitors_range"),
        "channels": r.get("channel_total") or {},
        "promo": {"split": bp.get("split"), "discount": bp.get("open_discount"), "gain": bp.get("visitors_gain"),
                  "roas": bp.get("roas"), "payback": bp.get("payback_week"), "budget": bp.get("budget")} if bp else None,
        "hub": {"reach_hub": hv.get("reach_hub"), "reach_broad": hv.get("reach_broad"),
                "ratio": hv.get("per_person_ratio")} if hv else None,
        "presc": (r.get("prescriptions") or [None])[0],
        "summary": diag_summary(r.get("diagnosis")),
        "calib": {"reduction": calib.get("error_reduction")} if calib else None,
        "revision": rev.get("business_idea"),
        "detail": {
            "weekly": r.get("weekly_visitors"), "repromo": r.get("repromo"), "assumptions": r.get("assumptions") or {},
            "scenarios": r.get("scenarios") or [], "budget_plan": bp, "hub_vs_broad": hv, "hubs": r.get("hubs") or [],
            "channel_share": r.get("channel_share") or {}, "group_awareness": r.get("group_awareness") or {},
            "group_count": r.get("group_count") or {},
            "prescriptions": r.get("prescriptions") or [], "diagnosis": r.get("diagnosis"),
            "revision_changes": rev.get("changes") or [],
        },
    }


def load_validation():
    for cand in [DATA / "validation_log.json", ROOT / "validation_log.json", ROOT / "outputs" / "validation_log.json"]:
        v = read_json(cand, [])
        if v:
            return v if isinstance(v, list) else [v]
    return []


def precomputed(codes, opts):
    """조건 없이 미리 계산된 결과가 있는 동만 고른 경우 그대로 사용"""
    if any(v for v in opts.values()):
        return None
    out = []
    for c in codes:
        r = read_json(DATA / f"result_{c}.json")
        if not r or not r.get("weekly_visitors"):
            return None
        out.append(r)
    return out


def loading_html(n):
    """계산 중 화면: 병아리가 걸어가는 로딩"""
    return f"""
<style>
.nr-load {{ display: flex; flex-direction: column; align-items: center; gap: 18px; padding: 72px 20px 88px; }}
.nr-road {{ position: relative; width: min(520px, 90%); height: 96px; border-bottom: 2px dashed #CFC8BB; overflow: hidden; }}
.nr-chick {{ position: absolute; bottom: 2px; left: 0; width: 64px; height: 72px; animation: nr-walk 4.5s linear infinite; }}
.nr-chick svg {{ width: 100%; height: 100%; animation: nr-bob .35s ease-in-out infinite alternate; }}
.nr-legL {{ transform-origin: 26px 58px; animation: nr-leg .35s ease-in-out infinite alternate; }}
.nr-legR {{ transform-origin: 38px 58px; animation: nr-leg .35s ease-in-out infinite alternate-reverse; }}
@keyframes nr-walk {{ from {{ transform: translateX(-70px); }} to {{ transform: translateX(min(560px, 95vw)); }} }}
@keyframes nr-bob {{ from {{ transform: translateY(0); }} to {{ transform: translateY(-4px); }} }}
@keyframes nr-leg {{ from {{ transform: rotate(-18deg); }} to {{ transform: rotate(18deg); }} }}
.nr-load-title {{ font-size: 20px; font-weight: 700; color: #111111; }}
.nr-load-title i {{ font-style: normal; display: inline-block; animation: nr-dot 1.2s infinite; }}
.nr-load-title i:nth-child(2) {{ animation-delay: .2s; }} .nr-load-title i:nth-child(3) {{ animation-delay: .4s; }}
@keyframes nr-dot {{ 0%, 60%, 100% {{ opacity: .2; }} 30% {{ opacity: 1; }} }}
.nr-load-sub {{ font-size: 15px; color: #595959; }}
@media (prefers-reduced-motion: reduce) {{ .nr-chick, .nr-chick svg, .nr-legL, .nr-legR, .nr-load-title i {{ animation: none; }} .nr-chick {{ left: 45%; }} }}
</style>
<div class="nr-load" role="status" aria-live="polite">
  <div class="nr-road">
    <div class="nr-chick" aria-hidden="true">
      <svg viewBox="0 0 64 72">
        <line class="nr-legL" x1="26" y1="58" x2="24" y2="70" stroke="#E08A00" stroke-width="3" stroke-linecap="round"/>
        <line class="nr-legR" x1="38" y1="58" x2="40" y2="70" stroke="#E08A00" stroke-width="3" stroke-linecap="round"/>
        <ellipse cx="32" cy="44" rx="20" ry="17" fill="#FFD54A"/>
        <circle cx="42" cy="24" r="13" fill="#FFD54A"/>
        <path d="M22 44 q8 -8 14 2 q-8 6 -14 -2z" fill="#F5B301"/>
        <circle cx="46" cy="21" r="2.2" fill="#111111"/>
        <path d="M54 24 l8 3 l-8 3 z" fill="#E08A00"/>
        <path d="M38 11 q3 -6 6 0" fill="none" stroke="#F5B301" stroke-width="2.5" stroke-linecap="round"/>
      </svg>
    </div>
  </div>
  <div class="nr-load-title">{n}개 동네에 소식을 퍼뜨리는 중이에요<i>.</i><i>.</i><i>.</i></div>
  <div class="nr-load-sub">동네당 30초쯤 걸려요</div>
</div>"""


def run_engine(codes, opts, idea):
    """조건에 맞게 엔진 실행. 같은 조건은 runs/<키>에 저장해 다시 쓰기"""
    key = hashlib.md5(json.dumps({"c": sorted(codes), "o": opts}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]
    folder = RUNS / key
    results = [read_json(folder / f"result_{c}.json") for c in codes]
    if all(results):
        return results, None
    folder.mkdir(parents=True, exist_ok=True)
    for name in ENGINE_FILES:
        src, dst = DATA / name, folder / name
        if src.exists() and not dst.exists():
            try:
                os.link(src, dst)
            except OSError:
                shutil.copy(src, dst)
    scenario = {"business_idea": idea, "candidate_dongs": codes, **opts}
    json.dump(scenario, open(folder / "scenario.json", "w", encoding="utf-8"), ensure_ascii=False)
    cmd = [sys.executable, str(ROOT / "engine" / "simulate.py"), "--data", f"runs/{key}", "--dongs", *codes]
    if not opts.get("budget"):
        cmd.append("--no-promo")
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=900)
    if proc.returncode != 0:
        return None, (proc.stderr or proc.stdout)[-1500:]
    return [read_json(folder / f"result_{c}.json") for c in codes], None


dong_map = load_dongs()
labels = list(dong_map.keys())
code_to_label = {v: k for k, v in dong_map.items()}
scenario0 = read_json(DATA / "scenario.json", {})
default_labels = [code_to_label[c] for c in ["1144066000", "1144071000", "1144069000"] if c in code_to_label]

# ---------- 첫 화면 제목 + 예시 캐러셀 ----------
weights = read_json(ROOT / "config" / "weights.json", {})
spw = weights.get("steps_per_week", 2)


RECENT = RUNS / "recent.json"


def home_examples():
    """가장 최근에 실제로 돌린 결과. 아직 없으면 기본 3개 동(서교·연남·망원1)"""
    rec = read_json(RECENT) or {}
    results = [read_json(Path(p)) for p in rec.get("paths", [])]
    results = [r for r in results if r and r.get("weekly_visitors")]
    idea = rec.get("idea")
    if not results:
        results = [read_json(DATA / f"result_{c}.json") for c in ["1144066000", "1144071000", "1144069000"]]
        results = [r for r in results if r and r.get("weekly_visitors")]
        idea = scenario0.get("business_idea") or "디저트 카페"
    return sorted([build_dong(r, spw) for r in results], key=lambda d: -(d["monthly"] or 0)), idea


def render_view(view, extra, height):
    base = {"view": view, "dong_total": len(labels), "people_total": len(load_people()), "flags": {},
            "conditions": [], "dongs": [], "validation": None, "validation_all": []}
    base.update(extra)
    html = VIEW.read_text(encoding="utf-8").replace("__DATA__", json.dumps(base, ensure_ascii=False))
    components.html(html, height=height, scrolling=False)


if page == "home":
    examples, examples_idea = home_examples()
    render_view("hero", {"dongs": examples, "idea": examples_idea}, 680)

# ---------- 입력: 한 단계씩 (필수 2개 -> 선택 4개) ----------
STEPS = [  # (키, 이름, 필수 여부, 질문)
    ("idea", "가게", True, "어떤 가게를 열 계획인가요?"),
    ("dongs", "후보 동네", True, "어느 동네를 비교해볼까요? (최대 3곳)"),
    ("place", "가게 자리", False, "동네 안 어디에 낼 계획인가요? 지도에서 눌러주세요"),
    ("hours", "영업시간", False, "몇 시부터 몇 시까지 여나요?"),
    ("days", "영업 요일", False, "어떤 요일에 여나요?"),
    ("cost", "월세·객단가", False, "월세와 손님 한 명이 쓰는 평균 금액은 얼마인가요?"),
    ("budget", "홍보 예산", False, "홍보에 얼마를 쓸 수 있나요?"),
]
ss = st.session_state
ss.setdefault("step", 0)
ss.setdefault("w", {"idea": "", "dongs": [], "place": {}, "open": None, "close": None, "days": DAYS[:], "rent": 0, "price": 0, "budget": 0})
ss["w"].setdefault("place", {})
w = ss["w"]


def to_opts(w):
    hours_ok = w["open"] is not None and w["close"] is not None and w["close"] > w["open"]
    return {
        "open_hours": [w["open"], w["close"]] if hours_ok else None,
        "open_days": w["days"] if w["days"] and len(w["days"]) < 7 else None,
        "rent": w["rent"] * 10000 if w["rent"] else None,
        "avg_price": w["price"] or None,
        "budget": w["budget"] * 10000 if w["budget"] else None,
        "store_locations": {dong_map[l]: list(p) for l, p in w.get("place", {}).items() if l in w["dongs"] and l in dong_map} or None,
    }


def summary(key):
    if key == "idea":
        return w["idea"]
    if key == "dongs":
        return ", ".join(d.split(" · ")[0] for d in w["dongs"])
    if key == "place":
        pl = {l: p for l, p in w.get("place", {}).items() if l in w["dongs"]}
        return ", ".join(f"{l.split(' · ')[0]} 지정" for l in pl) or "동 중심으로 계산"
    if key == "hours":
        o = to_opts(w)["open_hours"]
        return f"{o[0]}시 ~ {o[1]}시" if o else "정하지 않음"
    if key == "days":
        return "매일" if len(w["days"]) == 7 else ("·".join(w["days"]) or "정하지 않음")
    if key == "cost":
        parts = ([f"월세 {w['rent']:,}만 원"] if w["rent"] else []) + ([f"객단가 {w['price']:,}원"] if w["price"] else [])
        return ", ".join(parts) or "넣지 않음"
    return f"{w['budget']:,}만 원" if w["budget"] else "넣지 않음"


def tracker(cur):
    items = []
    for i, (key, name, req, _) in enumerate(STEPS):
        if i == 2:
            items.append('<span class="nr-sep">선택</span>')
        state = "done" if i < cur else ("now" if i == cur else "todo")
        mark = "✓" if state == "done" else str(i + 1)
        items.append(f'<span class="nr-step {state} {"opt" if not req else ""}"><b>{mark}</b>{name}</span>')
    return '<div class="nr-track">' + "".join(items) + "</div>"


def submit():
    ss.update(idea=w["idea"], picked=w["dongs"], opts=to_opts(w))
    st.query_params["page"] = "result"
    st.rerun()


def save_current(key):
    if key == "idea":
        w["idea"] = ss.get("in_idea", "").strip()
    elif key == "dongs":
        w["dongs"] = list(ss.get("in_dongs", []))
    elif key == "place":
        w["place"] = dict(ss.get("in_place", {}))
    elif key == "hours":
        w["open"], w["close"] = ss.get("in_open"), ss.get("in_close")
    elif key == "days":
        w["days"] = list(ss.get("in_days") or [])
    elif key == "cost":
        w["rent"], w["price"] = ss.get("in_rent", 0), ss.get("in_price", 0)
    elif key == "budget":
        w["budget"] = ss.get("in_budget", 0)


idea = w["idea"]
opts = to_opts(w)
if page == "home":
    with st.container(key="nr-search"):
        cur = ss.step
        st.markdown(tracker(cur), unsafe_allow_html=True)
        # 이미 답한 단계: 한 줄 요약 + 수정
        for i in range(min(cur, len(STEPS))):
            key, name, _, _ = STEPS[i]
            c1, c2, c3 = st.columns([1.3, 7, 1], vertical_alignment="center")
            c1.markdown(f'<span class="nr-sum-label">{name}</span>', unsafe_allow_html=True)
            c2.markdown(f'<span class="nr-sum-val">{summary(key)}</span>', unsafe_allow_html=True)
            if c3.button("수정", key=f"edit_{key}", type="tertiary"):
                ss.step = i
                st.rerun()

        if cur < len(STEPS):
            key, name, req, question = STEPS[cur]
            badge = '<span class="nr-badge req">필수</span>' if req else '<span class="nr-badge opt">선택</span>'
            st.markdown(f'<div class="nr-q">{badge}{question}</div>', unsafe_allow_html=True)
            filled = True
            if key == "idea":
                ss.setdefault("in_idea", w["idea"])
                st.text_input(name, key="in_idea", label_visibility="collapsed",
                              placeholder="예) 20대 대학생 대상 합리적인 가격의 디저트 카페")
                filled = bool(ss.get("in_idea", "").strip())
            elif key == "dongs":
                ss.setdefault("in_dongs", list(w["dongs"]))
                st.multiselect(name, labels, key="in_dongs", max_selections=3, label_visibility="collapsed",
                               placeholder="눌러서 고르거나 동 이름을 입력해 검색  예) 서교")
                filled = bool(ss.get("in_dongs"))
            elif key == "place":
                ss.setdefault("in_place", dict(w.get("place", {})))
                place = ss["in_place"]
                if folium is None:
                    st.markdown('<p class="nr-note">지도 부품이 설치되지 않아 동 중심으로 계산해요.</p>', unsafe_allow_html=True)
                elif not w["dongs"]:
                    st.markdown('<p class="nr-note">먼저 후보 동네를 골라주세요.</p>', unsafe_allow_html=True)
                else:
                    tabs = st.tabs([l.split(" · ")[0] for l in w["dongs"]])
                    for tab, lab in zip(tabs, w["dongs"]):
                        with tab:
                            code = dong_map[lab]
                            base = default_store_point(code)
                            pin = place.get(lab)
                            m = folium.Map(location=pin or base, zoom_start=16, tiles="OpenStreetMap", control_scale=True)
                            folium.CircleMarker(base, radius=6, color="#9A9A9A", fill=True, fill_opacity=0.6,
                                                tooltip="지정하지 않으면 여기(동 중심)로 계산").add_to(m)
                            if pin:
                                folium.CircleMarker(pin, radius=11, color="#111111", weight=4, fill=True,
                                                    fill_color="#F5B301", fill_opacity=1, tooltip="내 가게").add_to(m)
                            out = st_folium(m, key=f"map_{code}", height=380, use_container_width=True,
                                            returned_objects=["last_clicked"])
                            clicked = (out or {}).get("last_clicked")
                            if clicked and tuple(place.get(lab) or ()) != (clicked["lat"], clicked["lng"]):
                                place[lab] = (clicked["lat"], clicked["lng"])
                                st.rerun()
                            if pin:
                                c1, c2 = st.columns([6, 1.4], vertical_alignment="center")
                                c1.markdown(f'<span class="nr-sum-val">지정한 자리: 위도 {pin[0]:.5f}, 경도 {pin[1]:.5f}</span>', unsafe_allow_html=True)
                                if c2.button("동 중심으로", key=f"clear_{code}", type="tertiary"):
                                    place.pop(lab, None)
                                    st.rerun()
                            else:
                                st.markdown('<p class="nr-note">회색 점이 지금 계산에 쓰는 동 중심이에요. 실제로 낼 골목을 누르면 그 자리로 계산해요.</p>', unsafe_allow_html=True)
                filled = bool({l for l in place if l in w["dongs"]})
            elif key == "hours":
                hours = [None] + list(range(0, 25))
                fmt_h = lambda h: "정하지 않음" if h is None else f"{h}시"
                ss.setdefault("in_open", w["open"])
                ss.setdefault("in_close", w["close"])
                h1, h2 = st.columns(2)
                h1.selectbox("여는 시간", hours[:-1], key="in_open", format_func=fmt_h)
                h2.selectbox("닫는 시간", hours, key="in_close", format_func=fmt_h)
                o, c = ss.get("in_open"), ss.get("in_close")
                filled = o is not None and c is not None and c > o
                if o is not None and c is not None and c <= o:
                    st.markdown('<p class="nr-note">닫는 시간이 여는 시간보다 늦어야 해요</p>', unsafe_allow_html=True)
            elif key == "days":
                ss.setdefault("in_days", list(w["days"]))
                p1, p2, p3, _ = st.columns([1, 1, 1, 5])
                for col, (lab, ds) in zip((p1, p2, p3), (("매일", DAYS), ("평일만", DAYS[:5]), ("주말만", DAYS[5:]))):
                    if col.button(lab, key=f"preset_{lab}", type="tertiary"):
                        ss["in_days"] = list(ds)
                        st.rerun()
                if hasattr(st, "pills"):
                    st.pills(name, DAYS, selection_mode="multi", key="in_days", label_visibility="collapsed")
                else:
                    st.multiselect(name, DAYS, key="in_days", label_visibility="collapsed")
                on = [d for d in DAYS if d in (ss.get("in_days") or [])]
                off = [d for d in DAYS if d not in on]
                st.markdown(f'<p class="nr-days"><span class="on">영업 {"·".join(on) or "없음"}</span>'
                            f'<span class="off">쉬는 날 {"·".join(off) or "없음"}</span></p>', unsafe_allow_html=True)
                filled = len(on) > 0  # 매일 영업도 고른 것이라 '다음'으로
                if not on:
                    st.markdown('<p class="nr-note">하루 이상 골라주세요</p>', unsafe_allow_html=True)
            elif key == "cost":
                ss.setdefault("in_rent", w["rent"])
                ss.setdefault("in_price", w["price"])
                n1, n2 = st.columns(2)
                n1.number_input("월세 (만 원)", min_value=0, step=10, key="in_rent")
                n2.number_input("객단가 (원)", min_value=0, step=500, key="in_price")
                filled = bool(ss.get("in_rent") or ss.get("in_price"))
            elif key == "budget":
                ss.setdefault("in_budget", w["budget"])
                st.number_input("홍보 예산 (만 원)", min_value=0, step=10, key="in_budget")
                filled = bool(ss.get("in_budget"))

            last = cur == len(STEPS) - 1
            btns = []
            if cur > 0:
                btns.append(("prev", "← 이전", "secondary", False))
            if not last:
                no_day = key == "days" and not filled  # 요일을 하나도 안 고르면 넘어갈 수 없음
                btns.append(("next", "다음 →" if (req or filled or no_day) else "건너뛰기 →", "secondary", (req or no_day) and not filled))
            if cur >= 2:
                btns.append(("go", "결과 보기", "primary", False))
            cols = st.columns([1.2] * len(btns) + [max(0.5, 8 - 1.2 * len(btns))], gap="small")
            for col, (kind, label, btype, dis) in zip(cols, btns):
                if col.button(label, key=f"{kind}_{key}", type=btype, disabled=dis, use_container_width=True):
                    save_current(key)
                    if kind == "go":
                        submit()
                    ss.step = cur - 1 if kind == "prev" else cur + 1
                    st.rerun()
        else:
            if st.button("결과 보기", key="go_end", type="primary"):
                submit()

elif page == "result":
    with st.container(key="nr-bar"):
        c1, c2 = st.columns([8, 1.4], vertical_alignment="center")
        c1.markdown(f'<span class="nr-sum-val">{ss.get("idea") or ""}</span>', unsafe_allow_html=True)
        if c2.button("조건 바꾸기", key="back_home", type="secondary"):
            ss.step = max(ss.get("step", 2), 2)
            st.query_params["page"] = "home"
            st.rerun()

# ---------- 결과 계산 ----------
validation = load_validation()
payload = {"view": page, "dong_total": len(labels), "people_total": len(load_people()),
           "validation": None, "validation_all": validation, "flags": {}, "conditions": [], "dongs": []}
if validation:
    v = validation[-1]
    payload["validation"] = {"rank_corr": v.get("rank_corr"), "target": v.get("target"), "round": v.get("round")}

if page == "result":
    picked = ss.get("picked") or default_labels
    opts = ss.get("opts") or opts
    idea = ss.get("idea") or idea or "디저트 카페"
    codes = [dong_map[l] for l in picked if l in dong_map]
    results = precomputed(codes, opts)
    if results is None:
        loading = st.empty()
        loading.markdown(loading_html(len(codes)), unsafe_allow_html=True)
        results, err = run_engine(codes, opts, idea)
        loading.empty()
        if err:
            st.error("계산 중 문제가 생겼어요.")
            if st.query_params.get("debug") == "1":
                st.code(err)
            st.stop()
    payload["dongs"] = sorted([build_dong(r, spw) for r in results if r], key=lambda d: -(d["monthly"] or 0))
    payload["idea"] = idea
    # 첫 화면 '최근 예측'과 카드가 이 결과로 바뀌게 저장
    try:
        RUNS.mkdir(parents=True, exist_ok=True)
        paths = []
        for r in results:
            if not r:
                continue
            p = RUNS / "recent" / f"result_{r['dong_code']}.json"
            p.parent.mkdir(parents=True, exist_ok=True)
            json.dump(r, open(p, "w", encoding="utf-8"), ensure_ascii=False)
            paths.append(str(p))
        json.dump({"paths": paths, "idea": idea}, open(RECENT, "w", encoding="utf-8"), ensure_ascii=False)
    except OSError:
        pass
    payload["flags"] = {"promo": bool(opts.get("budget")), "breakeven": bool(opts.get("rent"))}
    cond = []
    if opts.get("open_hours"):
        cond.append(f"영업 {opts['open_hours'][0]}~{opts['open_hours'][1]}시")
    if opts.get("open_days"):
        cond.append("·".join(opts["open_days"]))
    if opts.get("rent"):
        cond.append(f"월세 {opts['rent'] // 10000:,}만 원")
    if opts.get("avg_price"):
        cond.append(f"객단가 {opts['avg_price']:,}원")
    if opts.get("budget"):
        cond.append(f"홍보 {opts['budget'] // 10000:,}만 원")
    if opts.get("store_locations"):
        cond.append(f"가게 자리 지정 {len(opts['store_locations'])}곳")
    payload["conditions"] = cond
elif page == "home":
    payload["dongs"] = examples
    payload["idea"] = examples_idea

html = VIEW.read_text(encoding="utf-8").replace("__DATA__", json.dumps(payload, ensure_ascii=False))
heights = {"home": 520, "result": 2900, "how": 1500}
components.html(html, height=heights.get(page, 1200), scrolling=False)

if st.query_params.get("debug") == "1":
    with st.expander("개발용 정보"):
        st.write({"page": page, "opts": opts, "가중치": weights.get("version"), "runs": str(RUNS)})
