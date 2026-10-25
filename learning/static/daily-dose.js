
"use strict";

(() => {
  const $ = (id) => document.getElementById(id);

  const configNode = $("daily-dose-config");

  if (!configNode) {
    console.error("Daily Dose configuration missing.");
    return;
  }

  const config = JSON.parse(configNode.textContent);

  const dashboard = config.dashboard || {};
  const words = config.words || [];
  const progress = config.progress || {};

  const level = config.level;
  const target = Number(config.settings?.masteryCorrect || 3);

  const PREVIEW_MS = 3000;

  let current = null;
  let previewTimer = null;
  let busy = false;
  let answered = false;

  let rotation = [];
  let rotationIndex = 0;
  let lastWordId = null;

  // A completed level opens in Replay Mode.
  const levelCard = (dashboard.levels || []).find(
    (item) => item.number === Number(level)
  );

  const replayMode = !config.course && Boolean(
    levelCard?.complete ||
    (words.length > 0 &&
      words.every((word) => progress[word.id]?.mastered === true))
  );

  // Replay tracks words attempted in this session.
  const replayCompleted = new Set();

  // ------------------------------------------
  // SCREEN HELPERS
  // ------------------------------------------

  function showScreen(id) {
    [
      "dashboard-screen",
      "level-screen",
      "complete-screen"
    ].forEach((screen) => {
      $(screen).classList.toggle(
        "hidden",
        screen !== id
      );
    });
  }

  function error(message) {
    $("app-error").textContent = message;
    $("app-error").classList.remove("hidden");
  }

  function clearError() {
    $("app-error").textContent = "";
    $("app-error").classList.add("hidden");
  }

  function dashboardUrl() {
    return config.urls.dashboard;
  }

  function goDashboard() {
    window.location.assign(dashboardUrl());
  }

  function goLevel(n) {
    window.location.assign(
      `${dashboardUrl()}?level=${encodeURIComponent(n)}`
    );
  }

  // ------------------------------------------
  // MASTERY
  // ------------------------------------------

  function isMastered(word) {
    return progress[word.id]?.mastered === true;
  }

  function streak(word) {
    if (isMastered(word)) {
      return target;
    }

    return Math.max(
      0,
      Math.min(
        target,
        Number(progress[word.id]?.recall_streak || 0)
      )
    );
  }

  function masteredCount() {
    return words.filter(isMastered).length;
  }

  // ------------------------------------------
  // DASHBOARD
  // ------------------------------------------

  function renderDashboard() {
    showScreen("dashboard-screen");

    $("total-words").textContent =
      dashboard.total_words || 0;

    $("mastered-words").textContent =
      dashboard.mastered_words || 0;

    $("completed-levels").textContent =
      dashboard.completed_levels || 0;

    $("overall-percent").textContent =
      `${dashboard.overall_percent || 0}%`;

    $("overall-fill").style.width =
      `${dashboard.overall_percent || 0}%`;

    $("total-levels").textContent =
      `${dashboard.total_levels || 0} levels`;

    const grid = $("levels-grid");
    grid.replaceChildren();

    const levels = dashboard.levels || [];

    $("no-levels").classList.toggle(
      "hidden",
      levels.length !== 0
    );

    levels.forEach((item) => {
      const button = document.createElement("button");

      button.type = "button";

      button.className =
        `level${
          item.complete
            ? " complete"
            : item.unlocked
              ? " active"
              : ""
        }`;

      button.disabled = !item.unlocked;

      const title = document.createElement("strong");
      title.textContent = `Level ${item.number}`;

      const count = document.createElement("span");
      count.textContent =
        `${item.mastered}/${item.total} mastered`;

      const status = document.createElement("span");

      status.textContent = item.complete
        ? "✓ Completed — Replay →"
        : item.unlocked
          ? "Continue →"
          : "Locked";

      button.append(title, count, status);

      if (item.unlocked) {
        button.addEventListener("click", () => {
          goLevel(item.number);
        });
      }

      grid.appendChild(button);
    });
  }

  // ------------------------------------------
  // LEVEL PROGRESS
  // ------------------------------------------

  function renderProgress() {
    if (replayMode) {
      const done = replayCompleted.size;

      const percent = words.length
        ? Math.round(100 * done / words.length)
        : 0;

      $("level-count").textContent =
        `${done}/${words.length} reviewed`;

      $("level-percent").textContent =
        `${percent}%`;

      $("level-fill").style.width =
        `${percent}%`;

      return;
    }

    const done = masteredCount();

    const percent = words.length
      ? Math.round(100 * done / words.length)
      : 0;

    $("level-count").textContent =
      `${done}/${words.length} mastered`;

    $("level-percent").textContent =
      `${percent}%`;

    $("level-fill").style.width =
      `${percent}%`;
  }

  // ------------------------------------------
  // WORD ROTATION
  // ------------------------------------------

  function nextWord() {
    if (config.course) return words.find(word => word.id === config.next_word_id) || null;
    // Replay: include mastered words, once each.
    if (replayMode) {
      return words.find(
        (word) => !replayCompleted.has(word.id)
      ) || null;
    }

    // Learn: rotate only unmastered words.
    const unfinished = words.filter(
      (word) => !isMastered(word)
    );

    if (!unfinished.length) {
      return null;
    }

    const remainingIds = new Set(
      unfinished.map((word) => word.id)
    );

    rotation = rotation.filter(
      (id) => remainingIds.has(id)
    );

    unfinished.forEach((word) => {
      if (!rotation.includes(word.id)) {
        rotation.push(word.id);
      }
    });

    if (rotationIndex >= rotation.length) {
      rotationIndex = 0;
    }

    if (
      rotation.length > 1 &&
      rotation[rotationIndex] === lastWordId
    ) {
      rotationIndex =
        (rotationIndex + 1) % rotation.length;
    }

    const id = rotation[rotationIndex];

    rotationIndex =
      (rotationIndex + 1) % rotation.length;

    return unfinished.find(
      (word) => word.id === id
    );
  }

  // ------------------------------------------
  // IMAGE
  // ------------------------------------------

  function setImage(word, onReady) {
    const image = $("word-image");
    const warning = $("image-warning");

    image.hidden = true;
    warning.classList.add("hidden");

    image.removeAttribute("src");

    if (!word.image_url) {
      warning.classList.remove("hidden");
      onReady();
      return;
    }

    image.onload = () => {
      image.hidden = false;
      onReady();
    };

    image.onerror = () => {
      warning.classList.remove("hidden");
      onReady();
    };

    image.src = word.image_url;
  }

  // ------------------------------------------
  // PREVIEW FOR 3 SECONDS
  // ------------------------------------------

  function startPreview(word) {
    clearTimeout(previewTimer);

    current = word;
    answered = false;

    const position =
      words.findIndex((w) => w.id === word.id) + 1;

    $("word-position").textContent =
      config.course ? `Word ${config.cycle_position} of ${config.cycle_total} in this cycle` : `Word ${position} of ${words.length}`;

    $("english-word").textContent =
      word.english;

    $("phase-text").textContent =
      replayMode
        ? "Replay — Memorize for 3 seconds"
        : "Memorize — 3 seconds";

    $("tagalog-preview").textContent =
      word.tagalog;

    $("preview-section").classList.remove("hidden");
    $("recall-section").classList.add("hidden");

    setImage(word, () => {
      if (current === word) {
        previewTimer = setTimeout(
          showRecall,
          PREVIEW_MS
        );
      }
    });
  }

  // ------------------------------------------
  // RECALL
  // ------------------------------------------

  function showRecall() {
    previewTimer = null;

    $("tagalog-preview").textContent = "";
    $("preview-section").classList.add("hidden");

    $("phase-text").textContent =
      "Recall from memory";

    $("recall-section").classList.remove("hidden");

    $("answer-input").value = "";
    $("answer-input").disabled = false;

    $("check-button").disabled = false;
    $("check-button").classList.remove("hidden");

    $("feedback").textContent = "";
    $("feedback").className = "feedback";

    $("continue-button").classList.add("hidden");

    $("answer-input").focus();
  }

  // ------------------------------------------
  // COMPLETE
  // ------------------------------------------

  function complete() {
    clearTimeout(previewTimer);

    showScreen("complete-screen");

    if (config.course) {
      $("complete-title").textContent = "Vocabulary learned!";
      const nextButton = $("next-level-button");
      nextButton.textContent = "Continue to practice and test →";
      nextButton.classList.remove("hidden");
      nextButton.onclick = () => window.location.assign(config.urls.continue);
      return;
    }
    const next = Number(level) + 1;
    const button = $("next-level-button");

    if (replayMode) {
      $("complete-title").textContent =
        `Level ${level} replay complete!`;

      button.textContent = "Replay level again →";
      button.classList.remove("hidden");

      button.onclick = () => {
        goLevel(level);
      };

      return;
    }

    if (next <= Number(dashboard.total_levels || 0)) {
      $("complete-title").textContent =
        `Level ${level} complete!`;

      button.textContent =
        `Start level ${next} →`;

      button.classList.remove("hidden");

      button.onclick = () => {
        goLevel(next);
      };
    } else {
      $("complete-title").textContent =
        "All vocabulary levels complete!";

      button.classList.add("hidden");
    }
  }

  // ------------------------------------------
  // ADVANCE
  // ------------------------------------------

  function advance() {
    if (busy) {
      return;
    }

    clearError();
    renderProgress();

    if (replayMode) {
      if (replayCompleted.size >= words.length) {
        complete();
        return;
      }
    } else if (
      !config.course && words.length &&
      masteredCount() === words.length
    ) {
      complete();
      return;
    }

    const word = nextWord();

    if (word) {
      startPreview(word);
    } else if (replayMode && words.length) {
      complete();
    } else {
      error(
        "No vocabulary available for this level."
      );
    }
  }

  // ------------------------------------------
  // CHECK ANSWER AND SAVE VIA FLASK
  // ------------------------------------------

  async function submitAnswer(event) {
    event.preventDefault();

    if (
      busy ||
      answered ||
      !current ||
      previewTimer !== null
    ) {
      return;
    }

    const answer =
      $("answer-input").value.trim();

    if (!answer) {
      return;
    }

    busy = true;

    $("check-button").disabled = true;
    clearError();

    try {
      const response = await (window.learningFetch || fetch)(
        config.urls.answer,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json"
          },
          body: JSON.stringify({
            word_id: current.id,
            answer: answer,
            level: Number(level),
            cycle_id: config.cycle_id
          })
        }
      );

      const result = await response.json();

      if (!response.ok || !result.ok) {
        if (result.next_url) {window.location.assign(result.next_url); return;}
        throw new Error(
          result.error ||
          "Could not save your answer."
        );
      }

      if (config.course) {
        if (result.next_url) {
          window.location.assign(result.next_url);
          return;
        }
        config.cycle_id = result.cycle_id;
        config.next_word_id = result.next_word_id;
        config.cycle_position = result.cycle_position;
        config.cycle_total = result.cycle_total;
      }
      answered = true;

      progress[current.id] = result.stats;

      if (result.dashboard) {
        Object.assign(
          dashboard,
          result.dashboard
        );
      }

      if (replayMode) {
        replayCompleted.add(current.id);
      }

      const feedback = $("feedback");

      if (result.correct) {
        feedback.textContent = replayMode
          ? "✓ Correct!"
          : result.word_mastered
            ? "✓ Correct — word mastered!"
            : `✓ Correct — ${streak(current)}/${target} consecutive.`;

        feedback.className = "feedback good";
      } else {
        feedback.textContent =
          `Incorrect. Correct answer: ${result.answer}`;

        feedback.className = "feedback bad";
      }

      $("answer-input").disabled = true;

      $("check-button").classList.add("hidden");

      const sessionComplete = replayMode
        ? replayCompleted.size >= words.length
        : words.length > 0 &&
          masteredCount() === words.length;

      $("continue-button").textContent =
        sessionComplete
          ? "Complete level →"
          : "Continue →";

      $("continue-button").classList.remove("hidden");

      lastWordId = current.id;

      renderProgress();

      $("continue-button").focus();

    } catch (e) {
      error(e.message);
      $("check-button").disabled = false;

    } finally {
      busy = false;
    }
  }

  // ------------------------------------------
  // EVENTS
  // ------------------------------------------

  $("back-dashboard").addEventListener(
    "click",
    goDashboard
  );

  $("complete-dashboard").addEventListener(
    "click",
    goDashboard
  );

  $("answer-form").addEventListener(
    "submit",
    submitAnswer
  );

  $("continue-button").addEventListener(
    "click",
    advance
  );

  window.addEventListener("pagehide", () => {
    clearTimeout(previewTimer);
  });

  // ------------------------------------------
  // START
  // ------------------------------------------

  if (level === null || level === undefined) {
    renderDashboard();
  } else {
    showScreen("level-screen");

    $("level-title").textContent =
      replayMode
        ? `Level ${level} — Replay`
        : `Level ${level}`;

    advance();
  }
})();
