"""The model Architect thinks with: a strong Gemini thinking model first, the next one if it cannot answer, then the platform's own
engine, and an honest error (never a made-up sentence) when none can."""
import dataclasses
import json
import types

import httpx
import pytest

import app.ai_services  # noqa: F401  (imported before any test swaps httpx's client: openai builds a class on it when it loads)
from app import architect_model
from app.architect_model import ArchitectModelError, ask
from app.config import get_config

pytestmark = pytest.mark.asyncio

REAL_CLIENT = httpx.AsyncClient


def config(key="test-key", models=("pro-x", "flash-y"), thinking="HIGH", attempt=30.0, total=50.0):
    base = get_config()
    return dataclasses.replace(base, llm=dataclasses.replace(base.llm, gemini_api_key=key, architect_models=models, architect_thinking=thinking,
                                                             architect_attempt_secs=attempt, architect_total_secs=total))


def reply_with(text, finish="STOP", thought=None):
    parts = ([{"text": thought, "thought": True}] if thought else []) + [{"text": text}]
    return {"candidates": [{"content": {"role": "model", "parts": parts}, "finishReason": finish}]}


def piece(text, thought=False, finish=None):
    """One chunk of a streamed reply: a bit of the answer, or (thought=True) a summary of the model's thinking."""
    candidate = {"content": {"role": "model", "parts": [{"text": text, **({"thought": True} if thought else {})}]}}
    if finish:
        candidate["finishReason"] = finish
    return {"candidates": [candidate]}


def sse(*chunks):
    """A streamed reply as Google sends it: one `data:` line per chunk."""
    return httpx.Response(200, content="".join(f"data: {json.dumps(c)}\r\n\r\n" for c in chunks).encode(), headers={"content-type": "text/event-stream"})


class Google:
    """A stand-in for Google's API. `script` is one entry per request, in order: a dict (answered 200), or (status, body), or an exception."""
    def __init__(self, monkeypatch, script):
        self.script, self.requests = list(script), []
        monkeypatch.setattr(architect_model.httpx, "AsyncClient",
                            lambda **kw: REAL_CLIENT(transport=httpx.MockTransport(self.handle), **kw))

    def handle(self, request: httpx.Request):
        self.requests.append(request)
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        if isinstance(step, httpx.Response):                    # a streamed reply, built by sse()
            return step
        status, body = step if isinstance(step, tuple) else (200, step)
        return httpx.Response(status, json=body)

    def body(self, n=0):
        return json.loads(self.requests[n].content)


# --- what is sent -------------------------------------------------------------------------------------------------------------

async def test_the_first_model_is_asked_to_think_hard_and_answer_in_json(monkeypatch):
    google = Google(monkeypatch, [reply_with('{"reply": "Hi"}', thought="let me think")])
    out = await ask("be Architect", "make me an agent", [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "Hi"}],
                    cfg=config(), answer_tokens=1200)
    assert out == '{"reply": "Hi"}'                                                  # the thinking is not part of the answer
    request, body = google.requests[0], google.body()
    assert request.url.path.endswith("/models/pro-x:generateContent")
    assert request.headers["x-goog-api-key"] == "test-key" and "test-key" not in str(request.url)       # in a header, not the address
    assert body["systemInstruction"]["parts"][0]["text"] == "be Architect"
    assert [(t["role"], t["parts"][0]["text"]) for t in body["contents"]] == [("user", "hello"), ("model", "Hi"), ("user", "make me an agent")]
    cfg = body["generationConfig"]
    assert cfg["responseMimeType"] == "application/json" and cfg["thinkingConfig"] == {"thinkingLevel": "HIGH"}
    assert cfg["maxOutputTokens"] >= 1200 + 8000                                      # the answer, and room to think before it


async def test_turns_start_with_the_person_and_neighbours_of_one_kind_become_one(monkeypatch):
    google = Google(monkeypatch, [reply_with("{}")])
    await ask("s", "third", [{"role": "assistant", "content": "welcome"}, {"role": "user", "content": "first"},
                             {"role": "user", "content": "second"}], cfg=config())
    turns = google.body()["contents"]
    assert [t["role"] for t in turns] == ["user"] and turns[0]["parts"][0]["text"] == "first\n\nsecond\n\nthird"


async def test_an_organizations_own_key_and_chosen_models_are_used(monkeypatch):
    google = Google(monkeypatch, [reply_with("{}")])
    await ask("s", "p", cfg=config(key="their-own-key", models=("their-model",), thinking="LOW"))
    assert google.requests[0].headers["x-goog-api-key"] == "their-own-key"
    assert google.requests[0].url.path.endswith("/models/their-model:generateContent")
    assert google.body()["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "LOW"}


async def test_a_model_that_thinks_its_own_way_is_asked_again_without_the_thinking_level(monkeypatch):
    google = Google(monkeypatch, [(400, {"error": {"message": "Unknown name thinkingLevel in thinking_config"}}), reply_with('{"ok": 1}')])
    assert await ask("s", "p", cfg=config(models=("old-model",))) == '{"ok": 1}'
    assert "thinkingConfig" in google.body(0)["generationConfig"] and "thinkingConfig" not in google.body(1)["generationConfig"]


# --- when the first model cannot answer ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("failure", [
    (429, {"error": {"message": "quota exceeded"}}),
    (503, {"error": {"message": "overloaded"}}),
    httpx.ReadTimeout("took too long"),
    reply_with("", finish="SAFETY"),                                                    # nothing to read
    reply_with('{"reply": "cut o', finish="MAX_TOKENS"),                                # a cut-off document is worse than none
])
async def test_the_next_model_answers_when_the_first_cannot(monkeypatch, failure):
    google = Google(monkeypatch, [failure, reply_with('{"reply": "from the second"}')])
    assert await ask("s", "p", cfg=config()) == '{"reply": "from the second"}'
    assert [r.url.path.split("/")[-1] for r in google.requests] == ["pro-x:generateContent", "flash-y:generateContent"]


class Strict:
    """The platform's own engine, one that reports its failures (as the Groq/OpenAI client does when asked to be strict)."""
    supports_strict = True

    def __init__(self, reply):
        self.reply, self.asked = reply, []

    async def generate(self, prompt, history, system_prompt, **kw):
        self.asked.append(kw)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def platform_engine(monkeypatch, engine):
    monkeypatch.setattr("app.ai_services.LLMEngineFactory.get_engine", staticmethod(lambda cfg=None: engine))
    return engine


async def test_the_platforms_own_engine_steps_in_when_no_gemini_model_can(monkeypatch):
    Google(monkeypatch, [(429, {"error": {"message": "quota"}}), (429, {"error": {"message": "quota"}})])
    engine = platform_engine(monkeypatch, Strict('{"reply": "from the engine"}'))
    assert await ask("s", "p", cfg=config(), answer_tokens=900) == '{"reply": "from the engine"}'
    assert engine.asked[0] == {"max_tokens": 900, "raw": True, "strict": True}       # untouched text, and a failure raises


async def test_without_a_gemini_key_the_platforms_engine_answers(monkeypatch):
    google = Google(monkeypatch, [])
    platform_engine(monkeypatch, Strict('{"reply": "engine"}'))
    assert await ask("s", "p", cfg=config(key="")) == '{"reply": "engine"}'
    assert google.requests == []                                                         # no point asking Google without a key


async def test_when_nothing_can_answer_it_says_so_instead_of_inventing_a_reply(monkeypatch):
    Google(monkeypatch, [(429, {"error": {"message": "quota exceeded"}}), httpx.ConnectError("no route")])
    platform_engine(monkeypatch, Strict(RuntimeError("Error code: 429 - rate limit")))
    with pytest.raises(ArchitectModelError) as caught:
        await ask("s", "p", cfg=config())
    message = str(caught.value)
    assert "pro-x answered 429: quota exceeded" in message and "flash-y" in message and "rate limit" in message


async def test_an_engine_that_swallows_its_failures_is_not_used_as_a_stand_in(monkeypatch):
    """GeminiLLM answers every failure with a customer-service sentence. Asking it would turn an outage into a believable reply."""
    class Swallows:
        async def generate(self, prompt, history, system_prompt, **kw):
            return "I understand your issue and I am checking our support system right away."

    Google(monkeypatch, [])
    platform_engine(monkeypatch, Swallows())
    with pytest.raises(ArchitectModelError) as caught:
        await ask("s", "p", cfg=config(key=""))
    assert "no Gemini key" in str(caught.value) and "swallows its own failures" in str(caught.value)


async def test_it_stops_trying_models_when_there_is_no_time_left(monkeypatch):
    clock = iter([0.0, 0.0, 49.0, 49.0])
    monkeypatch.setattr(architect_model, "time", types.SimpleNamespace(monotonic=lambda: next(clock)))      # not the event loop's clock
    google = Google(monkeypatch, [(503, {"error": {"message": "overloaded"}})])
    platform_engine(monkeypatch, Strict(RuntimeError("down")))
    with pytest.raises(ArchitectModelError) as caught:
        await ask("s", "p", cfg=config(total=50.0))
    assert len(google.requests) == 1 and "out of time" in str(caught.value)             # the second model was not started


async def test_a_model_gets_its_share_of_the_time_or_what_is_left_if_that_is_less(monkeypatch):
    """nginx gives up on a request after 60 seconds: every model together must answer before that."""
    seen = []
    clock = iter([0.0, 0.0, 35.0, 35.0])

    def client(**kw):
        seen.append(kw["timeout"])
        status = 503 if len(seen) == 1 else 200
        return REAL_CLIENT(transport=httpx.MockTransport(lambda request: httpx.Response(status, json=reply_with("{}"))), **kw)

    monkeypatch.setattr(architect_model.httpx, "AsyncClient", client)
    monkeypatch.setattr(architect_model, "time", types.SimpleNamespace(monotonic=lambda: next(clock)))
    await ask("s", "p", cfg=config(attempt=30.0, total=50.0))
    assert seen == [30.0, 15.0]                                    # the first its full share; the second only the 15 seconds that remain


def test_the_time_limits_fit_inside_nginxs_sixty_seconds_by_default():
    llm = get_config().llm
    assert 0 < llm.architect_attempt_secs <= llm.architect_total_secs <= 55


# --- the engine helper ---------------------------------------------------------------------------------------------------------

async def test_generate_raw_asks_to_be_strict_and_asks_for_less_of_an_engine_that_refuses():
    from app.ai_services import generate_raw

    class Picky:
        def __init__(self):
            self.asked = []

        async def generate(self, prompt, history, system_prompt, max_tokens=250, raw=False):       # accepts raw, not strict
            self.asked.append({"max_tokens": max_tokens, "raw": raw})
            return "ok"

    engine = Picky()
    assert await generate_raw(engine, "p", [], "s", 900) == "ok"
    assert engine.asked == [{"max_tokens": 900, "raw": True}]


# --- showing the model's thinking while it works -------------------------------------------------------------------------------------

class Heard:
    def __init__(self, fail=False):
        self.said, self.fail = [], fail

    async def __call__(self, text):
        self.said.append(text)
        if self.fail:
            raise RuntimeError("the screen is gone")


async def test_the_thinking_is_handed_over_as_it_comes_and_only_the_answer_is_returned(monkeypatch):
    google = Google(monkeypatch, [sse(piece("**Looking**\n\nThe person runs a clinic.", thought=True), piece("**Planning**\n\nTwo desks.", thought=True),
                                      piece('{"reply": "Hi'), piece(' there"}', finish="STOP"))])
    heard = Heard()
    out = await ask("be Architect", "make me an agent", cfg=config(), on_thought=heard)
    assert out == '{"reply": "Hi there"}'
    assert heard.said == ["**Looking**\n\nThe person runs a clinic.", "**Planning**\n\nTwo desks."]
    request = google.requests[0]
    assert request.url.path.endswith("/models/pro-x:streamGenerateContent") and request.url.params["alt"] == "sse"
    assert request.headers["x-goog-api-key"] == "test-key" and "test-key" not in str(request.url)
    assert google.body()["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "HIGH", "includeThoughts": True}


async def test_without_a_listener_nothing_is_streamed_and_no_summaries_are_asked_for(monkeypatch):
    google = Google(monkeypatch, [reply_with("{}")])
    await ask("s", "p", cfg=config())
    assert google.requests[0].url.path.endswith(":generateContent") and "includeThoughts" not in json.dumps(google.body())


async def test_a_listener_still_gets_a_plain_text_answer_when_json_is_not_wanted(monkeypatch):
    google = Google(monkeypatch, [sse(piece("**Drafting**", thought=True), piece("# Fees\n\nThe fee is "), piece("five hundred.", finish="STOP"))])
    heard = Heard()
    assert await ask("s", "p", cfg=config(thinking=""), on_thought=heard, json_output=False) == "# Fees\n\nThe fee is five hundred."
    cfg = google.body()["generationConfig"]
    assert "responseMimeType" not in cfg and cfg["thinkingConfig"] == {"includeThoughts": True}            # it may still show what it thinks


async def test_a_model_that_will_not_show_its_thinking_is_asked_again_without_asking_to_see_it(monkeypatch):
    google = Google(monkeypatch, [httpx.Response(400, json={"error": {"message": "includeThoughts is not supported in thinking_config"}}),
                                  sse(piece('{"ok": 1}', finish="STOP"))])
    heard = Heard()
    assert await ask("s", "p", cfg=config(models=("old",)), on_thought=heard) == '{"ok": 1}'
    assert google.body(0)["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "HIGH", "includeThoughts": True}
    assert google.body(1)["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "HIGH"} and heard.said == []


async def test_a_model_that_cannot_think_the_way_asked_at_all_is_asked_a_third_time_plainly(monkeypatch):
    google = Google(monkeypatch, [httpx.Response(400, json={"error": {"message": "Unknown name thinking_config"}}),
                                  httpx.Response(400, json={"error": {"message": "Unknown name thinking_config"}}), sse(piece("{}", finish="STOP"))])
    assert await ask("s", "p", cfg=config(models=("old",)), on_thought=Heard()) == "{}"
    assert "thinkingConfig" not in google.body(2)["generationConfig"] and len(google.requests) == 3


async def test_a_stream_that_runs_out_of_room_is_an_error_and_the_next_model_answers(monkeypatch):
    google = Google(monkeypatch, [sse(piece("**Thinking**", thought=True), piece('{"reply": "cut o', finish="MAX_TOKENS")),
                                  sse(piece('{"reply": "whole"}', finish="STOP"))])
    heard = Heard()
    assert await ask("s", "p", cfg=config(), on_thought=heard) == '{"reply": "whole"}'
    assert [r.url.path.split("/")[-1] for r in google.requests] == ["pro-x:streamGenerateContent", "flash-y:streamGenerateContent"]
    assert heard.said == ["**Thinking**"]                                         # what the first model thought was already shown


async def test_a_stream_that_says_no_is_an_error_with_the_reason(monkeypatch):
    Google(monkeypatch, [httpx.Response(429, json={"error": {"message": "quota exceeded"}}), httpx.Response(503, json={"error": {"message": "busy"}})])
    platform_engine(monkeypatch, Strict(RuntimeError("down")))
    with pytest.raises(ArchitectModelError) as caught:
        await ask("s", "p", cfg=config(), on_thought=Heard())
    assert "pro-x answered 429: quota exceeded" in str(caught.value) and "flash-y answered 503: busy" in str(caught.value)


async def test_a_listener_that_breaks_never_costs_the_answer(monkeypatch):
    Google(monkeypatch, [sse(piece("**Thinking**", thought=True), piece("{}", finish="STOP"))])
    assert await ask("s", "p", cfg=config(), on_thought=Heard(fail=True)) == "{}"


async def test_a_stream_that_never_finishes_gives_up_in_time_and_the_next_model_answers(monkeypatch):
    import asyncio

    async def endless():
        await asyncio.sleep(30)
        yield b"data: {}\r\n\r\n"

    google = Google(monkeypatch, [httpx.Response(200, content=endless()), sse(piece("{}", finish="STOP"))])
    assert await asyncio.wait_for(ask("s", "p", cfg=config(attempt=0.2), on_thought=Heard()), 5) == "{}"          # not after the 30 seconds the first one would take
    assert len(google.requests) == 2


async def test_lines_that_are_not_data_are_ignored(monkeypatch):
    stream = httpx.Response(200, content=b": keep-alive\r\n\r\ndata: not json\r\n\r\n" + sse(piece("{}", finish="STOP")).content)
    Google(monkeypatch, [stream])
    assert await ask("s", "p", cfg=config(), on_thought=Heard()) == "{}"
