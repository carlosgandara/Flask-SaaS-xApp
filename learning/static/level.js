const stepEl = document.querySelector(".step");
if (stepEl) {

  const STOP = new Set([
    "a","an","the","of","to","in","on","is","are","and","or","with",
    "your","you","at","by","from","their","they","it","that","this",
    "for","as","be","was","were"
  ]);

  function normalize(s) {
    return (s || "").toString().toLowerCase().trim()
      .normalize("NFD").replace(/[\u0300-\u036f]/g, "")
      .replace(/[^a-z0-9\s\/,]/g, " ")
      .replace(/\s+/g, " ")
      .trim();
  }

  function matches(userAnswer, correct, aliases) {
    const u = normalize(userAnswer);
    const c = normalize(correct);
    if (!u) return false;

    // direct match
    if (u === c) return true;

    // alias match
    if (aliases && aliases.length) {
      for (const a of aliases) {
        if (u === normalize(a)) return true;
      }
    }

    // split on / or , — accept any individual part
    const parts = correct.split(/[\/,]/).map(p => normalize(p)).filter(Boolean);
    if (parts.some(p => u === p)) return true;

    // key-word match: 50% of meaningful words must appear
    const keyTokens = c.split(/\s+/).filter(w => w.length > 2 && !STOP.has(w) && w !== "/");
    if (keyTokens.length === 0) return u.length > 0;
    const uTokens = new Set(u.split(/\s+/).filter(Boolean));
    const hits = keyTokens.filter(w => uTokens.has(w)).length;
    return hits / keyTokens.length >= 0.5;
  }

  async function postAnswer(word, correct, advance) {
    if (advance === undefined) advance = true;
    const r = await (window.learningFetch || fetch)("/answer", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ word: word, correct: correct, advance: advance })
    });
    return await r.json();
  }

  // -------- "Continue" buttons (scene / preview) --------
  document.querySelectorAll("[data-next]").forEach(btn => {
    btn.addEventListener("click", async () => {
      await postAnswer(null, true);
      window.location.reload();
    });
  });

  // -------- word step --------
  const wordInput = document.querySelector(".word-input");
  if (wordInput) {
    const answer = wordInput.dataset.answer;
    const aliases = wordInput.dataset.aliases ? JSON.parse(wordInput.dataset.aliases) : [];
    const btn = document.querySelector("[data-submit]");
    const feedback = stepEl.querySelector(".feedback");
    let locked = false;

    async function check() {
      if (locked) return;
      const ok = matches(wordInput.value, answer, aliases);

      if (ok) {
        locked = true;
        feedback.textContent = "✓ Correct";
        feedback.className = "feedback ok";
        wordInput.disabled = true;
        btn.disabled = true;
        setTimeout(async () => {
          await postAnswer(answer, true);
          window.location.reload();
        }, 500);
      } else {
        feedback.textContent = "✗ Try again";
        feedback.className = "feedback bad";
        wordInput.classList.add("shake");
        setTimeout(() => wordInput.classList.remove("shake"), 400);
        wordInput.select();

        const res = await postAnswer(answer, false);
        if (res.reload) {
          feedback.textContent = "Let's practice this again.";
          setTimeout(() => window.location.reload(), 900);
        }
      }
    }

    btn.addEventListener("click", check);
    wordInput.addEventListener("keydown", e => {
      if (e.key === "Enter") { e.preventDefault(); check(); }
    });
    wordInput.focus();
  }

  // -------- drill step --------
  const drillEl = document.querySelector(".drill");
  if (drillEl) {
    const words = JSON.parse(drillEl.dataset.words);
    const vocab = JSON.parse(drillEl.dataset.vocab);
    const queue = [];
    words.forEach(w => {
      queue.push({ word: w, dir: "en_to_tl" });
      queue.push({ word: w, dir: "tl_to_en" });
    });
    queue.sort(() => Math.random() - 0.5);

    let qi = 0;
    const promptEl = drillEl.querySelector(".drill-prompt");
    const inputEl = drillEl.querySelector(".drill-input");
    const feedbackEl = drillEl.querySelector(".feedback");
    const progressEl = drillEl.querySelector(".drill-progress");
    const checkBtn = document.querySelector("[data-drill-check]");

    function show() {
      if (qi >= queue.length) {
        postAnswer(null, true).then(() => window.location.reload());
        return;
      }
      const card = queue[qi];
      const w = vocab[card.word] || {};
      progressEl.textContent = (qi + 1) + " / " + queue.length;
      if (card.dir === "en_to_tl") {
        promptEl.textContent = w.english || card.word;
        inputEl.dataset.answer = card.word;
        inputEl.placeholder = "Type the Tagalog…";
      } else {
        promptEl.textContent = card.word;
        inputEl.dataset.answer = w.english || card.word;
        inputEl.placeholder = "Type the English…";
      }
      inputEl.value = "";
      inputEl.disabled = false;
      inputEl.classList.remove("correct", "wrong", "shake");
      feedbackEl.textContent = "";
      feedbackEl.className = "feedback";
      inputEl.focus();
    }

    checkBtn.addEventListener("click", async () => {
      const card = queue[qi];
      const w = vocab[card.word] || {};
      const answer = inputEl.dataset.answer;
      const aliases = w.aliases || [];
      const ok = matches(inputEl.value, answer, aliases);
      if (ok) {
        feedbackEl.textContent = "✓";
        feedbackEl.className = "feedback ok";
        inputEl.disabled = true;
        await postAnswer(card.word, true, false);
        setTimeout(() => { qi++; show(); }, 500);
      } else {
        feedbackEl.textContent = "✗ " + answer;
        feedbackEl.className = "feedback bad";
        inputEl.classList.add("shake");
        inputEl.value = "";
        setTimeout(() => inputEl.classList.remove("shake"), 400);
        await postAnswer(card.word, false, false);
      }
    });

    inputEl.addEventListener("keydown", e => {
      if (e.key === "Enter") { e.preventDefault(); checkBtn.click(); }
    });
    show();
  }

  // -------- dialogue step --------
  const dialogueEl = document.querySelector(".dialogue");
  if (dialogueEl) {
    const inputs = [...dialogueEl.querySelectorAll(".dialogue-input")];

    async function checkDialogue() {
      if (inputs.some(i => !i.value.trim())) {
        inputs.forEach(i => {
          if (!i.value.trim()) i.classList.add("empty");
        });
        return;
      }

      let allCorrect = true;
      inputs.forEach(inp => {
        const aliases = inp.dataset.aliases ? JSON.parse(inp.dataset.aliases) : [];
        inp.classList.remove("empty");
        if (matches(inp.value, inp.dataset.answer, aliases)) {
          inp.classList.add("correct");
          inp.classList.remove("wrong");
          inp.disabled = true;
        } else {
          inp.classList.add("wrong");
          inp.classList.remove("correct");
          allCorrect = false;
        }
      });

      if (allCorrect) {
        await postAnswer(null, true);
        setTimeout(() => window.location.reload(), 600);
      }
    }

    const checkBtn = document.querySelector("[data-dialogue-check]");
    if (checkBtn) checkBtn.addEventListener("click", checkDialogue);

    inputs.forEach(inp => {
      inp.addEventListener("keydown", e => {
        if (e.key === "Enter") { e.preventDefault(); checkDialogue(); }
      });
    });

    if (inputs[0]) inputs[0].focus();
  }
}