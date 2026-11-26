// The phone on the landing page: a scripted conversation about a medicine, played like a short video. The patient asks by voice
// (a waveform, then the words), the assistant types its answer, and the last question hands over to a doctor.
(function () {
  const root = document.getElementById("chatDemo");
  if (!root) return;

  const SCRIPT = [
    { who: "me", text: "Can I take this with milk?" },
    { who: "bot", text: "Take Oflazest OZ with plain water. The calcium in milk can stop the medicine being absorbed properly." },
    { who: "me", text: "And if I miss a dose?" },
    { who: "bot", text: "Take it when you remember, unless your next dose is due within a few hours. Never take two at once." },
    { who: "me", text: "I feel dizzy since this morning." },
    { who: "bot", text: "I am sorry to hear that. That is worth a doctor's view. I can connect you to the duty doctor now.", action: "Connect me to the duty doctor" },
  ];

  const chat = root.querySelector(".chat");
  const btn = root.querySelector(".chat-ctl button");
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  let playing = false, run = 0, started = false;
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const esc = (s) => s.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));

  async function wait(ms, myRun) {
    let left = ms;
    while (left > 0 && myRun === run) {
      if (playing) left -= 100;
      await sleep(100);
    }
  }
  const scroll = () => { chat.scrollTop = chat.scrollHeight; };

  function addBubble(cls, html) {
    const b = document.createElement("div");
    b.className = `msg ${cls}`;
    b.innerHTML = html;
    chat.appendChild(b);
    requestAnimationFrame(() => b.classList.add("in"));
    scroll();
    return b;
  }

  async function playOnce(myRun) {
    chat.innerHTML = "";
    await wait(600, myRun);
    for (const line of SCRIPT) {
      if (myRun !== run) return false;
      if (line.who === "me") {
        const b = addBubble("me", `<span class="voice-bars" aria-hidden="true"><i></i><i></i><i></i><i></i><i></i><i></i><i></i><i></i></span><span class="said"></span>`);
        await wait(1300, myRun);
        b.classList.add("spoken");
        b.querySelector(".said").textContent = line.text;
        scroll();
        await wait(700, myRun);
      } else {
        const b = addBubble("bot", `<span class="dots"><i></i><i></i><i></i></span>`);
        await wait(1000, myRun);
        const words = line.text.split(" ");
        b.innerHTML = `<span class="t"></span>`;
        for (let i = 0; i < words.length; i++) {
          if (myRun !== run) return false;
          b.querySelector(".t").textContent = words.slice(0, i + 1).join(" ");
          scroll();
          await wait(60, myRun);
        }
        if (line.action) { b.insertAdjacentHTML("beforeend", `<span class="act">${esc(line.action)}</span>`); scroll(); }
        await wait(1100, myRun);
      }
    }
    await wait(3200, myRun);
    return myRun === run;
  }

  async function loop() {
    const myRun = ++run;
    while (myRun === run) { if (!(await playOnce(myRun))) return; }
  }

  function setPlaying(on) { playing = on; btn.textContent = on ? "❚❚" : "▶"; btn.setAttribute("aria-label", on ? "Pause" : "Play"); root.classList.toggle("is-paused", !on); }
  btn.addEventListener("click", () => setPlaying(!playing));

  if (reduce) {
    SCRIPT.slice(0, 4).forEach((l) => addBubble(l.who === "me" ? "me spoken" : "bot", l.who === "me" ? `<span class="said">${esc(l.text)}</span>` : `<span class="t">${esc(l.text)}</span>`));
    root.querySelectorAll(".msg").forEach((m) => m.classList.add("in"));
    btn.hidden = true;
  } else {
    new IntersectionObserver((es) => es.forEach((e) => {
      if (e.isIntersecting) { setPlaying(true); if (!started) { started = true; loop(); } } else setPlaying(false);
    }), { threshold: 0.4 }).observe(root);
  }
})();
