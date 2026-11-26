import json
from types import SimpleNamespace

import pytest

from app.incident_flow import (IncidentFlow, Slots, State, compute_priority, parse_count, spell, spell_ticket)


# ─── pure helpers ───────────────────────────────────────────────────────────────

def S(**kw):
    return Slots(**kw)


@pytest.mark.parametrize("slots,expected", [
    (S(issue="VPN down", affected="VPN", scope="whole sales team", scope_count=40, impact="client demo in an hour"), "P1"),
    (S(issue="email down", affected="Outlook", scope="everyone in the company"), "P1"),
    (S(issue="printer jam", affected="printer", scope="about ten people", scope_count=10, impact="contracts due 5 PM"), "P2"),
    (S(issue="cannot log in", affected="ERP", scope="only me", impact="payroll deadline today"), "P2"),
    (S(issue="laptop won't boot", affected="laptop", scope="only me", impact="not urgent, can use another laptop"), "P3"),
    (S(issue="question about installing a font", affected="laptop", scope="only me"), "P4"),
])
def test_priority_rules(slots, expected):
    assert compute_priority(slots)[0] == expected


def test_negated_urgency_is_not_urgent():
    p, _ = compute_priority(S(issue="mouse stopped working", scope="only me", impact="not urgent at all"))
    assert p == "P3"


def test_parse_count_and_spelling():
    assert parse_count("about ten people") == 10 and parse_count("40 users") == 40 and parse_count("forty") == 40
    assert parse_count("only me") is None and parse_count(None) is None
    assert spell("E4521") == "E 4 5 2 1"
    assert spell_ticket("INC000322") == "I N C, 0 0 0 3 2 2"


def test_merge_never_erases_known_facts():
    a = S(issue="printer jam", name="Rahul")
    a.merge(S(since="9 AM", fix_outcome="trying"))
    a.merge(S())                                           # a later empty extraction
    assert (a.issue, a.name, a.since) == ("printer jam", "Rahul", "9 AM") and a.fix_outcome is None
    a.merge(S(wants_ticket=True))
    a.merge(S(wants_ticket=False))
    assert a.wants_ticket is True


# ─── the flow with scripted stand-ins for the LLMs and the ticket tool ──────────

class Scripted:
    """Extraction: returns the JSON mapped to the caller's latest line. Composition: a fixed spoken line."""

    def __init__(self, by_message=None, text="Okay, I understand. Could you tell me more?", fail=False):
        self.by_message, self.text, self.fail = by_message or {}, text, fail
        self.prompts = []

    async def generate(self, prompt, history, system_prompt, tools=None, **kw):
        self.prompts.append((prompt, system_prompt))
        if self.fail:
            raise RuntimeError("llm down")
        if "extract structured facts" in system_prompt.lower():
            last = prompt.strip().splitlines()[-1].removeprefix("CALLER: ")
            return json.dumps(self.by_message.get(last, {"topic": "new_incident"}))
        return self.text


class FakeTools:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    async def execute(self, name, args):
        self.calls.append((name, args))
        if self.fail:
            return {"result": "I'm sorry, I could not reach the ticketing system just now."}
        return {"result": "Incident Ticket Created Successfully! Your ticket number is INC000500. Priority: P2-High. Logged."}


def make_flow(extractions, tools=None, compose_text="That sounds frustrating. Could you tell me more?", extract_fail=False):
    extractor = Scripted(extractions, fail=extract_fail)
    composer = Scripted(text=compose_text)
    router = SimpleNamespace(
        roles=SimpleNamespace(classify=extractor, conversation_for=lambda e: composer),
        _llm_for_node=lambda nd: object(), rag_engine=None, tool_executor=tools or FakeTools(), llm=object())
    return IncidentFlow(router), composer


async def turn(flow, history, msg, key="call-1"):
    out = await flow.handle(session_key=key, user_message=msg, history=history, node_data={"system_prompt": "persona"},
                            caller_phone="+911234567890", agent_name="Assistant", agent_voice="Aarohi", agent_llm="groq",
                            current_node_id="n1", is_handoff=False)
    if out is not None:
        history += [{"role": "user", "content": msg}, {"role": "assistant", "content": out["text"]}]
    return out


PRINTER = {
    "My printer on the third floor stopped working": {"issue": "printer stopped working", "affected": "printer, third floor"},
    "It shows a paper jam since nine this morning": {"symptom": "paper jam error", "since": "9 AM"},
    "Only our floor, about ten people": {"scope": "about ten people", "scope_count": 10},
    "I tried restarting it, still jammed": {"tried": "restarted, still jammed", "fix_outcome": "not_worked"},
    "Contracts must go out before five today": {"impact": "contracts due before 5 PM"},
    "Rahul Sharma, employee ID E4521": {"name": "Rahul Sharma", "employee_id": "E4521"},
}


@pytest.mark.asyncio
async def test_full_guided_intake_asks_first_then_creates_one_complete_ticket():
    tools = FakeTools()
    flow, _ = make_flow(PRINTER, tools)
    h = []
    first = await turn(flow, h, "My printer on the third floor stopped working")
    assert "?" in first["text"] and not tools.calls                       # asks, does not raise a ticket yet
    await turn(flow, h, "It shows a paper jam since nine this morning")
    await turn(flow, h, "Only our floor, about ten people")
    fix = await turn(flow, h, "I tried restarting it, still jammed")
    assert not tools.calls
    impact = await turn(flow, h, "Contracts must go out before five today")
    ident = await turn(flow, h, "Rahul Sharma, employee ID E4521")
    assert not tools.calls and "right" in ident["text"].lower() and "high" in ident["text"].lower()   # read-back, P2 = high
    assert "E 4 5 2 1" in ident["text"]

    done = await turn(flow, h, "Yes, go ahead")
    assert len(tools.calls) == 1 and done["tool_called"] == "create_symphony_ticket"
    name, args = tools.calls[0]
    d = args["description"] if "description" in args else args["issue_description"]
    assert args["priority"] == "P2-High"
    for needle in ("printer", "third floor", "paper jam", "9 AM", "ten people", "contracts", "Rahul Sharma", "E4521"):
        assert needle.lower() in d.lower(), needle
    assert "I N C, 0 0 0 5 0 0" in done["text"] and "high priority" in done["text"]
    assert "SMS" not in done["text"] and "EOD" not in done["text"]          # no invented promises

    bye = await turn(flow, h, "No, that's all, thanks")
    assert bye["tool_called"] == "end_call" and len(tools.calls) == 1


@pytest.mark.asyncio
async def test_each_question_is_asked_at_most_the_allowed_number_of_times():
    flow, _ = make_flow({"Whatever": {"issue": "laptop slow", "affected": "laptop"}})
    h = []
    replies, snapshots = [], []
    for _ in range(11):
        replies.append(await turn(flow, h, "Whatever"))
        snapshots.append(dict(flow.states["call-1"].asked))
    for asked in snapshots:                                                       # no question is ever asked more than its limit
        assert asked.get("details", 0) <= 2 and asked.get("identity", 0) <= 2 and asked.get("impact", 0) <= 1
    assert sum(1 for r in replies if r["tool_called"] == "create_symphony_ticket") == 1   # a silent caller still gets exactly one ticket


@pytest.mark.asyncio
async def test_urgent_team_wide_outage_is_raised_without_a_confirmation_round():
    tools = FakeTools()
    flow, _ = make_flow({
        "VPN is down for the whole sales team, demo in an hour": {"issue": "VPN down", "affected": "VPN", "scope": "whole sales team",
                                                                    "scope_count": 40, "impact": "client demo in an hour", "wants_ticket": True},
        "Anita Desai E7788": {"name": "Anita Desai", "employee_id": "E7788"},
    }, tools)
    h = []
    ask = await turn(flow, h, "VPN is down for the whole sales team, demo in an hour")
    assert "?" in ask["text"] and not tools.calls                           # still needs a name and ID
    done = await turn(flow, h, "Anita Desai E7788")
    assert len(tools.calls) == 1 and tools.calls[0][1]["priority"] == "P1-Critical" and "critical" in done["text"]


@pytest.mark.asyncio
async def test_caller_who_fixed_it_gets_no_ticket_and_a_clean_goodbye():
    tools = FakeTools()
    flow, _ = make_flow({
        "Wifi keeps dropping": {"issue": "wifi dropping", "affected": "laptop wifi", "symptom": "disconnects", "since": "today", "scope": "only me"},
        "Yes that worked, it's connected now": {"fix_outcome": "worked"},
    }, tools)
    h = []
    await turn(flow, h, "Wifi keeps dropping")
    await turn(flow, h, "Yes that worked, it's connected now")
    bye = await turn(flow, h, "Great, thanks, that's all")
    assert not tools.calls and bye["tool_called"] == "end_call"


@pytest.mark.asyncio
async def test_status_check_and_human_requests_fall_through_to_the_generic_flow():
    flow, _ = make_flow({"Status of INC000321?": {"topic": "status_check"}, "Get me a person": {"topic": "human"}})
    h = []
    assert await turn(flow, h, "Status of INC000321?") is None
    assert await turn(flow, h, "Get me a person") is None


@pytest.mark.asyncio
async def test_no_extraction_and_no_history_means_fall_back_to_the_generic_flow():
    flow, _ = make_flow({}, extract_fail=True)
    assert await turn(flow, [], "my printer is broken") is None


@pytest.mark.asyncio
async def test_failed_ticket_creation_is_reported_honestly():
    tools = FakeTools(fail=True)
    flow, _ = make_flow({"Printer": {"issue": "printer", "affected": "printer", "scope": "only me", "name": "Ravi", "employee_id": "E1",
                                      "wants_ticket": True, "impact": "none"}}, tools)
    h = []
    await turn(flow, h, "Printer")                       # read-back
    out = await turn(flow, h, "Yes, go ahead")
    assert tools.calls and "could not reach" in out["text"].lower()
    assert "INC" not in out["text"] and flow.states["call-1"].ticket_id is None


@pytest.mark.asyncio
async def test_rejecting_the_read_back_asks_what_to_change_then_confirms_again():
    flow, _ = make_flow({**PRINTER, "No wait, it's the second floor": {"affected": "printer, second floor"}})
    h = []
    for m in list(PRINTER):
        await turn(flow, h, m)
    rb = flow.states["call-1"]
    assert rb.awaiting_confirm
    ask = await turn(flow, h, "No")
    assert "change" in ask["text"].lower()
    again = await turn(flow, h, "No wait, it's the second floor")
    assert "second floor" in again["text"] and flow.states["call-1"].awaiting_confirm


@pytest.mark.asyncio
async def test_a_new_problem_after_a_ticket_starts_a_new_intake():
    tools = FakeTools()
    flow, _ = make_flow({**PRINTER, "Also my VPN is not connecting": {"issue": "VPN not connecting", "affected": "VPN"}}, tools)
    h = []
    for m in list(PRINTER) + ["Yes, go ahead"]:
        await turn(flow, h, m)
    assert flow.states["call-1"].ticket_id == "INC000500"
    out = await turn(flow, h, "Also my VPN is not connecting")
    assert out is not None and "?" in out["text"] and flow.states["call-1"].ticket_id is None


@pytest.mark.asyncio
async def test_a_caller_who_never_confirms_is_not_read_back_to_forever():
    tools = FakeTools()
    flow, _ = make_flow({"Printer": {"issue": "printer", "affected": "printer", "scope": "only me", "name": "Ravi",
                                      "employee_id": "E1", "wants_ticket": True, "impact": "none"}}, tools)
    h = []
    replies = [await turn(flow, h, "Printer") for _ in range(4)]
    read_backs = sum(1 for r in replies if "shall i go ahead" in r["text"].lower())
    assert read_backs == 3 and len(tools.calls) == 1


# ─── same problem after a ticket, late identity, goodbye ────────────────────────

class NoteTools(FakeTools):
    _tools = {"update_symphony_ticket": {}}

    async def execute(self, name, args):
        self.calls.append((name, args))
        if name == "update_symphony_ticket":
            return {"result": "Ticket INC000500 has been updated."}
        return {"result": "Incident Ticket Created Successfully! Your ticket number is INC000500. Priority: P2-High. Logged."}


@pytest.mark.asyncio
async def test_same_problem_after_ticket_does_not_create_a_duplicate_and_late_identity_is_appended():
    tools = NoteTools()
    ext = {"VPN is down for everyone": {"issue": "VPN down", "affected": "VPN", "scope": "everyone", "scope_count": 40,
                                        "impact": "client demo in an hour", "wants_ticket": True},
           "It's Anita Desai, E7788": {"name": "Anita Desai", "employee_id": "E7788", "issue": "VPN down", "affected": "VPN"}}
    flow, _ = make_flow(ext, tools)
    h = []
    await turn(flow, h, "VPN is down for everyone")
    for _ in range(3):                                                  # name never given: ticket is eventually raised anyway
        await turn(flow, h, "VPN is down for everyone")
        if flow.states["call-1"].ticket_id:
            break
    assert flow.states["call-1"].ticket_id == "INC000500"
    flow.states["call-1"].slots.name = None
    flow.states["call-1"].slots.employee_id = None
    out = await turn(flow, h, "It's Anita Desai, E7788")
    creates = [c for c in tools.calls if c[0] != "update_symphony_ticket"]
    updates = [c for c in tools.calls if c[0] == "update_symphony_ticket"]
    assert len(creates) == 1 and len(updates) == 1 and "E7788" in updates[0][1]["note"]
    assert "added your details" in out["text"]


def test_same_problem_helper():
    from app.incident_flow import same_problem
    assert same_problem(S(issue="VPN down", affected="VPN"), S(issue="VPN not connecting", affected="VPN"))
    assert not same_problem(S(issue="printer jam", affected="printer"), S(issue="Outlook crashing", affected="email client"))


@pytest.mark.asyncio
async def test_plain_goodbye_without_open_intake_ends_cleanly():
    flow, _ = make_flow({})
    out = await turn(flow, [], "Thanks, bye")
    assert out is not None and out["tool_called"] == "end_call" and "SMS" not in out["text"]


@pytest.mark.asyncio
async def test_late_identity_without_the_update_tool_is_answered_honestly():
    tools = FakeTools()                                                       # no update tool registered (gated off by default)
    flow, _ = make_flow({"VPN is down for everyone": {"issue": "VPN down", "affected": "VPN", "scope": "everyone", "scope_count": 40,
                                                       "impact": "demo in an hour", "wants_ticket": True},
                         "Anita Desai E7788": {"name": "Anita Desai", "employee_id": "E7788", "issue": "VPN down", "affected": "VPN"}}, tools)
    h = []
    for _ in range(4):
        await turn(flow, h, "VPN is down for everyone")
        if flow.states["call-1"].ticket_id:
            break
    flow.states["call-1"].slots.name = None
    flow.states["call-1"].slots.employee_id = None
    out = await turn(flow, h, "Anita Desai E7788")
    assert [c[0] for c in tools.calls] == ["create_symphony_ticket"] or len(tools.calls) == 1
    assert "can't edit the ticket" in out["text"] and out["tool_called"] is None


# ─── latency: parallel guard gate and extraction skipping ───────────────────────

@pytest.mark.asyncio
async def test_a_flagged_turn_never_creates_a_ticket():
    from app.incident_flow import GuardBlocked
    tools = FakeTools()
    flow, _ = make_flow({"Printer": {"issue": "printer", "affected": "printer", "scope": "only me", "name": "Ravi",
                                      "employee_id": "E1", "wants_ticket": True, "impact": "none"}}, tools)
    h = []
    await turn(flow, h, "Printer")                                            # read-back
    async def blocked():
        return False
    with pytest.raises(GuardBlocked):
        await flow.handle(session_key="call-1", user_message="Yes, go ahead", history=h, node_data={"system_prompt": "p"},
                          caller_phone="+911234567890", agent_name="A", agent_voice="V", agent_llm="groq",
                          current_node_id="n1", is_handoff=False, gate=blocked)
    assert not tools.calls


@pytest.mark.asyncio
async def test_plain_yes_to_the_read_back_skips_the_extraction_call():
    tools = FakeTools()
    flow, _ = make_flow({"Printer": {"issue": "printer", "affected": "printer", "scope": "only me", "name": "Ravi",
                                      "employee_id": "E1", "wants_ticket": True, "impact": "none"}}, tools)
    extractor = flow.router.roles.classify
    h = []
    await turn(flow, h, "Printer")
    before = len(extractor.prompts)
    done = await turn(flow, h, "Yes, that's correct")
    assert len(extractor.prompts) == before and done["tool_called"] == "create_symphony_ticket"


@pytest.mark.asyncio
async def test_speculative_wording_is_used_only_when_the_guess_matches_the_decision():
    flow, composer = make_flow({**PRINTER, "Never mind that": {}})
    h = []
    for m in list(PRINTER)[:5]:
        await turn(flow, h, m)
    stats = dict(flow.spec_stats)
    assert stats["hit"] + stats["miss"] >= 1                       # it did try to guess ahead
    # a caller who answers something else than asked makes the guess miss; the reply is still composed for the real action
    flow2, composer2 = make_flow({"My laptop is slow": {"issue": "laptop slow", "affected": "laptop"}, "Hmm": {}})
    h2 = []
    await turn(flow2, h2, "My laptop is slow")
    out = await turn(flow2, h2, "Hmm")
    assert out is not None and "?" in out["text"]
    assert flow2.spec_stats["hit"] + flow2.spec_stats["miss"] + flow2.spec_stats["none"] >= 2


# ─── lessons from the first live call ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_failed_ticket_creation_waits_for_a_yes_before_retrying_and_makes_no_invented_claims():
    tools = FakeTools(fail=True)
    flow, _ = make_flow({"Printer": {"issue": "stopped working", "affected": "printer", "scope": "only me", "name": "Ravi",
                                      "employee_id": "E1", "wants_ticket": True, "impact": "none"}}, tools)
    h = []
    await turn(flow, h, "Printer")
    out = await turn(flow, h, "Yes, go ahead")
    assert len(tools.calls) == 1 and "notified" not in out["text"].lower()
    out2 = await turn(flow, h, "Hmm okay")                                  # not a yes: no silent retry
    assert len(tools.calls) == 1
    tools.fail = False
    await turn(flow, h, "Yes please")
    assert len(tools.calls) == 2


@pytest.mark.asyncio
async def test_unknown_ticket_status_is_reported_honestly(monkeypatch):
    from app import summit_mcp_server
    from app.tool_executor import ToolExecutor

    async def not_found(ticket_id):
        return {"status": "NOT_FOUND", "ticket_id": ticket_id, "error": "none"}
    monkeypatch.setattr(summit_mcp_server, "get_incident_description", not_found)
    out = await ToolExecutor()._get_symphony_ticket_status("1268999")
    assert "could not find" in out.lower() and "Assigned" not in out and "SLA" not in out


# ─── lessons from the second live call ──────────────────────────────────────────

def test_first_line_fix_waits_for_a_symptom():
    flow, _ = make_flow({})
    st = State()
    st.slots = S(issue="desktop hangs", affected="desktop", scope="only me")
    st.asked = {"details": 2, "affected": 1}
    assert flow.decide(st, "+911234567890")[0] != "first_line_fix"      # nothing concrete to give advice about yet
    st.slots.symptom = "freezes at the Windows login screen"
    assert flow.decide(st, "+911234567890")[0] == "first_line_fix"


@pytest.mark.asyncio
async def test_questions_after_the_ticket_get_an_answer_not_the_same_stock_line():
    tools = FakeTools()
    flow, composer = make_flow({**PRINTER, "Is my email in the ticket?": {"topic": "new_incident", "issue": "printer stopped working", "affected": "printer"}},
                               tools, compose_text="Yes, your contact details are on the ticket. Anything else I can help with?")
    h = []
    for m in list(PRINTER) + ["Yes, go ahead"]:
        await turn(flow, h, m)
    assert flow.states["call-1"].ticket_id == "INC000500"
    out = await turn(flow, h, "Is my email in the ticket?")
    assert out is not None and "contact details are on the ticket" in out["text"] and len(tools.calls) == 1
