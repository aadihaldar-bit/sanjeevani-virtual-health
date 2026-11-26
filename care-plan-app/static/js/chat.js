// Text assistant under the care plan: ask about the medicines in writing and get a short answer from the plan.
// The server (/api/v1/chat) holds the plan, the safety rules and the emergency check; this file only shows the conversation.
"use strict";

let chatPlanId = null;
let chatTurns = [];
let chatBusy = false;

const CHAT_STARTERS = ["Can I take it with milk?", "What if I miss a dose?", "Any foods to avoid?", "How long do I take it?"];

function chatBubble(role, text, extra = "") {
  const b = document.createElement("div");
  b.className = `cmsg ${role === "user" ? "me" : "bot"} ${extra}`;
  b.innerHTML = `<span class="ct"></span>`;
  b.querySelector(".ct").textContent = text;
  $("chatList").appendChild(b);
  $("chatList").scrollTop = $("chatList").scrollHeight;
  return b;
}

function chatActions(bubble, emergency) {
  const row = document.createElement("div");
  row.className = "cact";
  const prefill = () => {
    const d = latestApiResponse || {};
    return { phone: (d.patient_context || {}).mobile_number, doctor: ((d.ai_clinical_summary || {}).doctor_name || "").toLowerCase(), reason: "Follow-up on a question from the care plan" };
  };
  row.innerHTML = (emergency ? `<a class="btn btn-danger btn-sm" href="tel:998">${icon("phone")}Call 998</a>` : "")
    + `<button type="button" class="btn btn-dark btn-sm">${icon("calendar")}Book a doctor visit</button>`
    + (typeof VOICE_CONNECTED !== "undefined" && VOICE_CONNECTED ? `<button type="button" class="btn btn-secondary btn-sm">${icon("phone")}Have the assistant call me</button>` : "");
  const [book, call] = row.querySelectorAll("button");
  book.addEventListener("click", () => openApptSheet(prefill()));
  if (call) call.addEventListener("click", () => startRealCall());
  if (emergency) bubble.classList.add("is-urgent");
  bubble.appendChild(row);
  $("chatList").scrollTop = $("chatList").scrollHeight;
}

async function sendChat(e, preset) {
  if (e) e.preventDefault();
  if (chatBusy || !chatPlanId) return;
  const input = $("chatInput");
  const text = (preset || input.value).trim();
  if (!text) return;
  input.value = "";
  chatBusy = true;
  $("chatSend").disabled = true;
  $("chatChips").hidden = true;
  chatBubble("user", text);
  chatTurns.push({ role: "user", text });
  const typing = chatBubble("bot", "", "is-typing");
  typing.innerHTML = `<span class="dots"><i></i><i></i><i></i></span>`;
  try {
    const r = await fetch("/api/v1/chat", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prescription_id: chatPlanId, messages: chatTurns }) });
    const d = await r.json().catch(() => ({}));
    typing.remove();
    if (!r.ok) throw new Error(d.detail || "the assistant could not answer");
    const b = chatBubble("assistant", d.reply);
    chatTurns.push({ role: "assistant", text: d.reply });
    if (d.doctor) chatActions(b, d.emergency);
  } catch (err) {
    typing.remove();
    chatTurns.pop();
    chatBubble("assistant", err.message.charAt(0).toUpperCase() + err.message.slice(1) + ".", "is-error");
  } finally {
    chatBusy = false;
    $("chatSend").disabled = false;
    input.focus();
  }
}
window.sendChat = sendChat;

function renderChat(data) {
  const card = $("chatCard");
  if (!card) return;
  chatPlanId = data.prescription_id || null;
  chatTurns = [];
  card.hidden = !chatPlanId;
  $("chatList").innerHTML = "";
  const name = ((data.patient_context || {}).patient_name || "").split(" ").find((w) => w && !/^(master|miss|mr|mrs|ms|dr|baby)\.?$/i.test(w)) || "";
  chatBubble("assistant", `${name ? `Hi ${name}, ` : "Hi, "}I can answer questions about the medicines in this plan. What would you like to know?`);
  $("chatChips").hidden = false;
  $("chatChips").innerHTML = CHAT_STARTERS.map((q) => `<button type="button" onclick="sendChat(null, ${JSON.stringify(q).replace(/"/g, "&quot;")})">${esc(q)}</button>`).join("");
}
window.renderChat = renderChat;

function focusChat() {
  const card = $("chatCard");
  if (!card || card.hidden) return false;
  card.scrollIntoView({ behavior: "smooth", block: "center" });
  setTimeout(() => $("chatInput").focus(), 400);
  return true;
}
window.focusChat = focusChat;
