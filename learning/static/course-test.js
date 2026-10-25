"use strict";
(() => {
  const $ = id => document.getElementById(id);
  const api = $("test-app").dataset.api;
  let state, busy = false, timer, deadline;
  const buttons = [$("practice"), $("final"), $("resume")];
  async function request(data) {
    const response = await (window.learningFetch || fetch)(api, {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(data)});
    if (!response.ok) throw new Error(response.status === 403 ? "Finish the lesson, vocabulary, and untimed practice before taking the final test." : "Could not continue. Resume your test or start a new practice round.");
    return response.json();
  }
  async function act(action, extra = {}) {
    if (busy) return;
    busy = true;
    buttons.forEach(b => b.disabled = true);
    $("test-error").textContent = "";
    clearInterval(timer);
    try {
      const result = await request({action, attempt:state?.attempt, index:state?.index, ...extra});
      state = result;
      if (action === "answer" && state.mode === "practice") {
        $("feedback").textContent = state.feedback.correct ? "Correct!" : `Correct answer: ${state.feedback.answer}`;
        $("continue").hidden = false;
        $("choices").querySelectorAll("button").forEach(b => b.disabled = true);
      } else await render();
    } catch(error) {
      $("test-error").textContent = error.message;
      $("choices").querySelectorAll("button").forEach(b => b.disabled = true);
      $("test-actions").hidden = false;
    } finally {busy = false; buttons.forEach(b => b.disabled = false); $("final").disabled = !(state?.practice_done || state?.mode === "final"); $("resume").hidden = !state;}
  }
  async function render() {
    $("continue").hidden = true;
    $("feedback").textContent = "";
    $("result").hidden = !state.done;
    $("question-panel").hidden = state.done;
    $("test-actions").hidden = !state.done;
    if (state.done) {
      $("result-title").textContent = state.mode === "practice" ? `Practice complete — ${state.score}/20. You can now take the final test.` : `${state.score}/20 — ${state.passed ? "Passed!" : "Review and try again"}`;
      $("review").replaceChildren();
      for (const item of state.review) {
        const p = document.createElement("p");
        p.textContent = `${item.prompt} → ${item.answer}${item.expired ? " (time ran out)" : ""}`;
        $("review").append(p);
      }
      $("next-level").hidden = !state.next_url;
      if (state.next_url) $("next-level").href = state.next_url;
      return;
    }
    $("question-count").textContent = `${state.mode === "practice" ? "Untimed practice" : "Final test"} · ${state.index + 1}/20 · ${state.question.kind}`;
    $("timer").textContent = state.mode === "practice" ? "Take your time" : "4.0 seconds";
    $("question").textContent = state.question.prompt;
    $("choices").replaceChildren();
    state.question.options.forEach((option, index) => {
      const button = document.createElement("button");
      button.className = "button secondary"; button.textContent = option; button.disabled = true;
      button.addEventListener("click", () => act("answer", {choice:index}));
      $("choices").append(button);
    });
    // Two frames let the browser paint the question before the server starts its clock.
    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    const sentAt = performance.now();
    const result = await request({action:"ready", attempt:state.attempt, index:state.index});
    state = result;
    $("choices").querySelectorAll("button").forEach(b => b.disabled = false);
    if (state.mode === "final") {
      deadline = sentAt + state.remaining_ms;
      timer = setInterval(() => {
        const left = Math.max(0, deadline - performance.now());
        $("timer").textContent = `${(left / 1000).toFixed(1)} seconds`;
        if (!left) {clearInterval(timer); act("answer", {choice:null});}
      }, 40);
    }
  }
  $("practice").onclick = () => act("start", {mode:"practice"});
  $("final").onclick = () => act("start", {mode:"final"});
  $("resume").onclick = async () => {
    // Attempt identity lives on the server; refresh resumes through the saved ID.
    if (!state) {
      try { state = await request({action:"current"}); } catch(error) { $("test-error").textContent=error.message; return; }
    }
    act("resume");
  };
  $("continue").onclick = () => {busy=true; render().catch(error => {$("test-error").textContent=error.message; $("test-actions").hidden=false;}).finally(() => busy=false);};
})();
