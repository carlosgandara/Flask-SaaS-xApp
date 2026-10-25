let activeSeconds = 0;
let lastActivity = Date.now();

const IDLE_LIMIT = 60 * 1000;

function markActive() {
  lastActivity = Date.now();
}

["mousemove", "keydown", "click", "touchstart"].forEach(event => {
  document.addEventListener(event, markActive, { passive: true });
});

setInterval(() => {
  const visible = document.visibilityState === "visible";
  const recentlyActive = Date.now() - lastActivity < IDLE_LIMIT;

  if (visible && recentlyActive) {
    activeSeconds++;
  }
}, 1000);


async function saveStudyTime(keepalive = false) {
  if (activeSeconds === 0) return;

  const seconds = activeSeconds;
  activeSeconds = 0;

  try {
    const response = await (window.learningFetch || fetch)("/study-time", {
      method: "POST",
      keepalive,
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify({ seconds })
    });
    if (!response.ok) throw new Error('Study time was not saved');
  } catch (err) {
    activeSeconds += seconds;
  }
}


setInterval(saveStudyTime, 30000);

document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    saveStudyTime(true);
  }
});

window.addEventListener("pagehide", () => {
  // Beacon cannot send the CSRF header required by the combined app.
  saveStudyTime(true);
});
