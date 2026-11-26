"""The doctor directory, the symptom guide and the slot arithmetic behind the Sanjeevani Appointment Desk.

Everything here is pure: it reads data/sanjeevani_care.json and does sums on dates and fees, so the tools that speak to the caller
(sanjeevani_desk.py) only have to say what these functions found. A slot is only ever offered if it falls inside a doctor's hours,
is in the future and is not already booked; the agent is told never to say a time that did not come from here.
"""
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from functools import lru_cache
from typing import Dict, Iterable, List, Optional, Set, Tuple

_DAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
_DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
_CHILD_CUES = ("year old", "years old", "year-old", "month old", "months old", "my child", "my son", "my daughter", "my kid",
               "baby", "infant", "toddler", "newborn")


@lru_cache(maxsize=1)
def directory() -> dict:
    with open(os.path.join(os.path.dirname(__file__), "data", "sanjeevani_care.json"), encoding="utf-8") as f:
        return json.load(f)


def tz():
    from .followup_scheduler import resolve_tz
    return resolve_tz(directory().get("timezone"))


# ── who treats what ─────────────────────────────────────────────────────────────────────────
def match_specialty(problem: str) -> Tuple[str, int]:
    """(specialty, score) for what the caller described. A child cue goes to Pediatrics; no match falls back to General Medicine."""
    p = (problem or "").lower()
    if any(c in p for c in _CHILD_CUES):
        return "Pediatrics", 99
    best, best_score = "General Medicine", 0
    for name, spec in directory()["specialties"].items():
        # General Medicine is the fallback, so a specific complaint outweighs a general one ("fever and swollen tonsils" is ENT)
        weight = 2 if name == "General Medicine" else 3
        score = sum(weight for kw in spec["keywords"] if re.search(rf"\b{re.escape(kw)}\b", p))
        if name.lower() in p:
            score += 3
        if score > best_score:
            best, best_score = name, score
    return best, best_score


def doctors_for(specialty: str, seniority: str = "") -> List[dict]:
    """The doctors of a specialty, senior consultants first. `seniority` of "senior" or "standard" narrows it."""
    want = (seniority or "").strip().lower()
    docs = [d for d in directory()["doctors"] if d["specialty"].lower() == (specialty or "").lower()]
    if want in ("senior", "standard"):
        docs = [d for d in docs if d["seniority"] == want]
    return sorted(docs, key=lambda d: (d["seniority"] != "senior", d["fee"]))


def find_doctor(name: str) -> Optional[dict]:
    n = re.sub(r"^dr\.?\s*", "", (name or "").strip().lower())
    if not n:
        return None
    for d in directory()["doctors"]:
        dn = re.sub(r"^dr\.?\s*", "", d["name"].lower())
        if n == dn or n in dn or dn in n:
            return d
    return None


# ── when a doctor can be seen ───────────────────────────────────────────────────────────────
def parse_availability(rules: Iterable[str]) -> List[Tuple[int, time, time]]:
    """["Mon 10:30-15:30"] -> [(0, 10:30, 15:30)]"""
    out = []
    for rule in rules or []:
        m = re.match(r"\s*([A-Za-z]{3})\w*\s+(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})", rule)
        if m and m.group(1).lower() in _DAYS:
            out.append((_DAYS[m.group(1).lower()], time(int(m.group(2)), int(m.group(3))), time(int(m.group(4)), int(m.group(5)))))
    return out


@dataclass
class Slot:
    start_utc: datetime            # naive UTC, the way the database stores it
    label: str                     # what is said aloud: "Monday 26 October at 10:30 AM"
    key: str                       # what the booking tool takes back: local time as 2026-10-26T10:30


def _as_utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def label_for(local: datetime) -> str:
    hour = local.hour % 12 or 12
    return f"{_DAY_NAMES[local.weekday()]} {local.day} {local.strftime('%B')} at {hour}:{local.minute:02d} {'AM' if local.hour < 12 else 'PM'}"


def next_slots(doctor: dict, now_utc: datetime, taken: Optional[Set[datetime]] = None, count: int = 2,
               lead_minutes: int = 120, horizon_days: int = 21, spread: bool = True) -> List[Slot]:
    """The next `count` free slots of a doctor. `taken` holds the (naive UTC) starts already booked. With `spread`, the slots
    are on different days when the doctor has more than one day free, so the caller is not offered two times a half hour apart."""
    zone = tz()
    step = int(directory().get("slot_minutes", 30))
    earliest = _as_utc(now_utc).astimezone(zone) + timedelta(minutes=lead_minutes)
    rules = parse_availability(doctor.get("availability"))
    taken = taken or set()
    found: List[Slot] = []
    seen_days: Set = set()
    for offset in range(horizon_days + 1):
        day = (earliest + timedelta(days=offset)).date()
        for wd, start, end in sorted(r for r in rules if r[0] == day.weekday()):
            cur = datetime.combine(day, start, tzinfo=earliest.tzinfo)
            stop = datetime.combine(day, end, tzinfo=earliest.tzinfo)
            while cur + timedelta(minutes=step) <= stop:
                utc = cur.astimezone(timezone.utc).replace(tzinfo=None)
                if cur >= earliest and utc not in taken and not (spread and day in seen_days):
                    found.append(Slot(utc, label_for(cur), cur.strftime("%Y-%m-%dT%H:%M")))
                    seen_days.add(day)
                    if len(found) >= count:
                        return found
                cur += timedelta(minutes=step)
    return found


def slot_from_key(doctor: dict, key: str, now_utc: datetime, taken: Optional[Set[datetime]] = None) -> Optional[Slot]:
    """The slot a caller chose, if it really is one of the doctor's free slots; otherwise None (never a made-up time)."""
    m = re.match(r"\s*(\d{4})-(\d{2})-(\d{2})[T ](\d{1,2}):(\d{2})", key or "")
    if not m:
        return None
    zone = tz()
    local = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4)), int(m.group(5)), tzinfo=zone)
    utc = local.astimezone(timezone.utc).replace(tzinfo=None)
    if utc in (taken or set()) or _as_utc(utc) < _as_utc(now_utc):
        return None
    for wd, start, end in parse_availability(doctor.get("availability")):
        if wd == local.weekday() and start <= local.time() and (local + timedelta(minutes=int(directory().get("slot_minutes", 30)))).time() <= end \
                and ((local.hour * 60 + local.minute) - (start.hour * 60 + start.minute)) % int(directory().get("slot_minutes", 30)) == 0:
            return Slot(utc, label_for(local), local.strftime("%Y-%m-%dT%H:%M"))
    return None


# ── what it costs ───────────────────────────────────────────────────────────────────────────
def money(amount: float) -> str:
    n = int(round(amount))
    return f"{n} dirhams"


def quote(fee: float, insurer: str = "") -> dict:
    """What the visit costs. A known insurer pays its share of the fee; the patient pays the rest. An insurer we do not know is
    not guessed at: the whole fee is payable and `accepted` is False."""
    name = (insurer or "").strip()
    rates: Dict[str, float] = directory()["insurers"]
    key = next((k for k in rates if k.lower() == name.lower()), None) if name else None
    if not name or name.lower() in ("none", "no", "self", "self pay", "self-pay", "cash", "no insurance"):
        return {"insurer": "", "accepted": True, "coverage": 0.0, "covered": 0.0, "payable": float(fee), "fee": float(fee)}
    if key is None:
        return {"insurer": name, "accepted": False, "coverage": 0.0, "covered": 0.0, "payable": float(fee), "fee": float(fee)}
    covered = round(fee * rates[key], 2)
    return {"insurer": key, "accepted": True, "coverage": rates[key], "covered": covered, "payable": round(fee - covered, 2), "fee": float(fee)}


# ── the pharmacy ────────────────────────────────────────────────────────────────────────────
@lru_cache(maxsize=1)
def pharmacy() -> dict:
    with open(os.path.join(os.path.dirname(__file__), "data", "sanjeevani_pharmacy.json"), encoding="utf-8") as f:
        return json.load(f)


def _kind(text: Optional[str]) -> Optional[str]:
    """liquid, solid or None (unknown) from a form or pack description: syrup, tablet, capsule, strip, bottle, sachet..."""
    t = (text or "").lower()
    if re.search(r"syrup|suspension|solution|drops|liquid|bottle|syp", t):
        return "liquid"
    if re.search(r"tablet|capsule|strip|tab|cap|caplet", t):
        return "solid"
    if re.search(r"sachet|powder", t):
        return "powder"
    if re.search(r"cream|gel|ointment|lotion", t):
        return "topical"
    return None


def _mg(text: Optional[str]) -> Optional[float]:
    m = re.search(r"(\d+(?:\.\d+)?)\s*mg", (text or "").lower())
    return float(m.group(1)) if m else None


def catalogue_entry(drug_name: Optional[str], form: Optional[str] = None, strength: Optional[str] = None) -> Optional[dict]:
    """The catalogue line for a medicine as written on a prescription (by name or alias), or None. Never guessed: no match, no price.
    A name alone is not enough when the prescription says more: a syrup is not priced as the tablet of the same drug, and a different
    strength is a different product."""
    n = re.sub(r"[^a-z0-9 ]", " ", (drug_name or "").lower())
    n = re.sub(r"\s+", " ", n).strip()
    if not n:
        return None
    want_kind = _kind(form) or _kind(drug_name)
    want_mg = _mg(strength) or _mg(drug_name)
    for e in pharmacy()["catalogue"]:
        names = [e["name"].lower()] + [a.lower() for a in e.get("aliases", [])]
        if not any(x and (x in n or n in x) for x in names):
            continue
        have_kind = _kind(e["name"]) or _kind(e.get("pack"))
        if want_kind and have_kind and want_kind != have_kind:
            continue                                                     # same drug, different form
        have_mg = _mg(e["name"])
        if want_mg is not None and have_mg is not None and abs(want_mg - have_mg) > 1e-6:
            continue                                                     # same drug, different strength
        return e
    return None


def order_total(items: Iterable[dict]) -> dict:
    """{subtotal, delivery_fee, total, unpriced}: medicines not in the catalogue are not priced here; a pharmacist prices them."""
    cfg = pharmacy()
    subtotal = sum(float(i["price"]) * int(i.get("quantity") or 1) for i in items if i.get("price") is not None)
    unpriced = [i.get("drug_name") for i in items if i.get("price") is None]
    fee = 0.0 if subtotal >= cfg["free_delivery_over"] else float(cfg["delivery_fee"])
    return {"subtotal": subtotal, "delivery_fee": fee if subtotal else 0.0, "total": subtotal + (fee if subtotal else 0.0), "unpriced": unpriced}


def delivery_slots(now_utc: datetime, count: int = 3) -> List[Slot]:
    """The next delivery windows: from tomorrow, two a day. Same-day delivery is not offered."""
    zone = tz()
    local = _as_utc(now_utc).astimezone(zone)
    out: List[Slot] = []
    day = local.date()
    for offset in range(1, 8):
        d = day + timedelta(days=offset)
        for window in pharmacy()["delivery_windows"]:
            a, b = window.split("-")
            ah, am = map(int, a.split(":"))
            bh, bm = map(int, b.split(":"))
            start = datetime(d.year, d.month, d.day, ah, am, tzinfo=zone)
            end_label = f"{bh % 12 or 12}{'' if bm == 0 else ':%02d' % bm} {'AM' if bh < 12 else 'PM'}"
            start_label = f"{ah % 12 or 12}{'' if am == 0 else ':%02d' % am} {'AM' if ah < 12 else 'PM'}"
            label = f"{_DAY_NAMES[start.weekday()]} {start.day} {start.strftime('%B')} between {start_label} and {end_label}"
            out.append(Slot(start.astimezone(timezone.utc).replace(tzinfo=None), label, start.strftime("%Y-%m-%dT%H:%M")))
            if len(out) >= count:
                return out
    return out


def delivery_slot_from_key(now_utc: datetime, key: str) -> Optional[Slot]:
    """The delivery window a caller chose, if it is one we offer; otherwise None."""
    return next((s for s in delivery_slots(now_utc, count=14) if s.key == (key or "").strip()), None)
