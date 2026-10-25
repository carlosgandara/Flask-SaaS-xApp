"use strict";
(() => {
  const $ = id => document.getElementById(id);
  const config = JSON.parse($("word-match-config").textContent);
  let board = config.initial, busy = false, settling = false, previewing = false, timer;
  async function request(url, data = {}) {
    const response = await (window.learningFetch || fetch)(url, {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(data)});
    if (!response.ok) throw new Error("Could not save this turn. Resume the round to continue.");
    return response.json();
  }
  function render() {
    $("match-board").replaceChildren();
    $("match-complete").hidden = !board?.done;
    $("new-round").textContent = board ? "New round" : "Start matching";
    $("new-round").disabled = busy || settling || previewing;
    $("match-score").textContent = `${board?.pairs || 0} / 10 pairs · ${board?.attempts || 0} attempts`;
    if (!board) return;
    for (const [index, card] of board.cards.entries()) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `match-card${card.text ? " face-up" : ""}${card.matched ? " matched" : ""}`;
      button.dataset.card = card.id;
      button.textContent = card.text || "?";
      button.setAttribute("aria-label", card.text ? `${card.text}${card.matched ? ", matched" : ""}` : `Face-down card ${index + 1}`);
      if (card.text) {
        const side = document.createElement("span");
        side.className = "card-side";
        side.textContent = card.matched ? "✓ MATCHED" : card.side === "english" ? "ENGLISH" : "LEARNING LANGUAGE";
        button.append(side);
      }
      button.disabled = busy || settling || previewing || Boolean(card.text);
      button.addEventListener("click", () => flip(card.id));
      $("match-board").append(button);
    }
  }
  function fail(error) {
    clearTimeout(timer);
    $("match-error").textContent = error.message;
    $("resume-round").hidden = false;
    // Unknown network outcomes must be reconciled before another flip.
    settling = Boolean(board);
  }
  async function showPreview(words) {
    previewing = true;
    $("word-preview").hidden = false;
    $("match-board").hidden = true;
    $("preview-words").replaceChildren();
    for (const word of words) {
      const p = document.createElement("p");
      p.textContent = `${word.word} — ${word.english}`;
      $("preview-words").append(p);
    }
    $("preview-countdown").textContent = "Take a quick look…";
    const response = await request(config.flip, {board_id:board.id, action:"preview"});
    timer = setTimeout(() => {
      previewing = false;
      $("word-preview").hidden = true;
      $("match-board").hidden = false;
      render();
    }, response.remaining_ms + 40);
  }
  function settleMismatch() {
    settling = true;
    render();
    timer = setTimeout(async () => {
      try {
        const response = await request(config.flip, {board_id:board.id, action:"sync"});
        board = response.board;
        settling = false;
        render();
      } catch(error) { fail(error); }
    }, 1250);
  }
  async function flip(id) {
    if (busy || settling || previewing) return;
    busy = true;
    $("match-error").textContent = "";
    render();
    try {
      const response = await request(config.flip, {board_id:board.id, card_id:id});
      board = response.board;
      $("match-feedback").textContent = board.done ? "All 10 pairs matched!" : response.matched === true ? "A pair! Keep going." : response.matched === false ? "These don’t match. Remember them and try again." : "Now find its meaning in the other language.";
      if (response.matched === false) settleMismatch();
    } catch(error) { fail(error); }
    finally {busy = false; render();}
  }
  async function begin(resume = false) {
    if (busy) return;
    clearTimeout(timer);
    busy = true; settling = false; previewing = false;
    $("match-error").textContent = "";
    $("resume-round").hidden = true;
    $("word-preview").hidden = true;
    $("match-board").hidden = false;
    render();
    try {
      const response = await request(resume ? config.resume : config.start);
      board = response.board;
      $("match-feedback").textContent = "Find all ten pairs. There’s no timer.";
      if (response.preview.length) await showPreview(response.preview);
      else if (board.cards.filter(c => c.text && !c.matched).length === 2) settleMismatch();
    } catch(error) { fail(error); }
    finally {busy = false; render();}
  }
  $("new-round").addEventListener("click", () => begin());
  $("resume-round").addEventListener("click", () => begin(true));
  window.addEventListener("pagehide", () => clearTimeout(timer));
  render();
  if (board) begin(true);
})();
