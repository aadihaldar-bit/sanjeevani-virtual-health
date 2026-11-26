// The monitor on the landing page: a scripted voice call that plays on a loop, with the orb, live captions and the plan beside it.
(function () {
  const root = document.getElementById("monitor");
  if (!root) return;

  const SCRIPT = [
    { who: "agent", med: 0, text: "Hello Sonika, this is the Sanjeevani care assistant. I have your new prescription. Shall we go through it together?" },
    { who: "you", med: null, text: "Yes please. When do I take the metformin?" },
    { who: "agent", med: 0, text: "Metformin 500 milligrams, twice a day. One after breakfast and one after dinner, so it sits well on your stomach." },
    { who: "you", med: null, text: "And the other tablet?" },
    { who: "agent", med: 1, text: "Atorvastatin 20 milligrams, once at night. Your HbA1c blood test is in three months, and we can send someone to your home for it." },
    { who: "you", med: null, text: "Can I take it with milk?" },
    { who: "agent", med: 1, text: "Yes, that is fine. If you ever miss a dose, take it when you remember, but never two together." },
  ];

  const orb = root.querySelector(".orb");
  const cap = root.querySelector(".caption");
  const bar = root.querySelector(".mon-bar i");
  const meds = root.querySelectorAll(".mon-med");
  const btn = root.querySelector(".mon-ctl button");
  const clock = root.querySelector(".mon-clock");
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  let playing = false, run = 0, started = false;
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const mmss = (s) => `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;

  function show(line) {
    cap.className = "caption" + (line.who === "you" ? " is-you" : "");
    cap.innerHTML = `<span class="who">${line.who === "agent" ? "Care assistant" : "Sonika"}</span><span class="words"></span>`;
    orb.dataset.state = line.who === "agent" ? "speaking" : "listening";
    meds.forEach((m, i) => m.classList.toggle("on", i === line.med));
  }

  async function play(myRun) {
    const total = SCRIPT.reduce((n, l) => n + l.text.length, 0);
    let done = 0, t0 = Date.now();
    const tick = setInterval(() => { if (clock) clock.textContent = mmss(Math.floor((Date.now() - t0) / 1000)); }, 500);
    for (const line of SCRIPT) {
      if (myRun !== run) break;
      show(line);
      const words = line.text.split(" "), slot = line.who === "agent" ? 230 : 190, el = cap.querySelector(".words");
      for (let i = 0; i < words.length; i++) {
        if (myRun !== run) break;
        while (!playing && myRun === run) await sleep(150);
        el.textContent = words.slice(0, i + 1).join(" ");
        done += words[i].length + 1;
        bar.style.width = Math.min(100, (done / total) * 100) + "%";
        await sleep(slot);
      }
      orb.dataset.state = "thinking";
      await sleep(line.who === "agent" ? 700 : 500);
    }
    clearInterval(tick);
    return myRun === run;
  }

  async function loop() {
    const myRun = ++run;
    while (myRun === run) {
      bar.style.width = "0%";
      if (!(await play(myRun))) return;
      orb.dataset.state = "idle";
      await sleep(1600);
    }
  }

  function setPlaying(on) { playing = on; btn.textContent = on ? "❚❚" : "▶"; btn.setAttribute("aria-label", on ? "Pause" : "Play"); if (!on && orb) orb.dataset.state = "idle"; }
  btn.addEventListener("click", () => setPlaying(!playing));

  if (reduce) {
    const last = SCRIPT[2];
    show(last); cap.querySelector(".words").textContent = last.text; orb.dataset.state = "idle"; bar.style.width = "40%"; btn.hidden = true;
  } else {
    // Start when it scrolls into view, pause when it leaves.
    new IntersectionObserver((es) => es.forEach((e) => {
      if (e.isIntersecting) { setPlaying(true); if (!started) { started = true; loop(); } } else setPlaying(false);
    }), { threshold: 0.45 }).observe(root);
  }

  // The monitor grows a little as it arrives, the way product shots do on apple.com.
  const onScroll = () => {
    const r = root.getBoundingClientRect(), vh = window.innerHeight;
    root.style.setProperty("--p", Math.max(0, Math.min(1, (vh - r.top) / (vh * 0.75))).toFixed(3));
  };
  if (!reduce) { addEventListener("scroll", onScroll, { passive: true }); onScroll(); }
})();
