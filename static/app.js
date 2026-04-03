const adhocStatus = document.getElementById("adhoc-status");
const adhocParticipantInput = document.getElementById("adhoc-participant");
const addParticipantButton = document.getElementById("add-participant-btn");
const actionStatus = document.getElementById("action-status");
const nextButton = document.getElementById("next-btn");
const resultsHeader = document.getElementById("results-header");
const drawButton = document.getElementById("draw-btn");
const redrawButton = document.getElementById("redraw-btn");
const confettiCanvas = document.getElementById("confetti-canvas");
const resultsBody = document.getElementById("results-body");

let appState = {
  prizes: [],
  results: [],
  remainingCount: 0,
  csvPath: null,
  isBusy: false,
  currentPrizeIndex: -1,
};

let confettiCtx = null;
let confettiParticles = [];
let confettiFrame = null;

function setStatus(element, message, isError = false) {
  element.textContent = message || "";
  element.classList.toggle("error", isError);
}

function hasActivePrize() {
  return appState.prizes.length > 0 && appState.currentPrizeIndex >= 0 && appState.currentPrizeIndex < appState.prizes.length;
}

function getSelectedPrize() {
  if (!hasActivePrize()) {
    return null;
  }
  return appState.prizes[appState.currentPrizeIndex];
}

function setControlsEnabled(enabled) {
  const allowActions = enabled && !appState.isBusy;
  nextButton.disabled = !allowActions || appState.prizes.length < 2 || appState.currentPrizeIndex <= 0;
  drawButton.disabled = !allowActions;
  redrawButton.disabled = !allowActions;
  addParticipantButton.disabled = !allowActions;
}

function initConfettiCanvas() {
  if (!confettiCanvas) {
    return;
  }
  confettiCtx = confettiCanvas.getContext("2d");
  const resizeCanvas = () => {
    confettiCanvas.width = window.innerWidth;
    confettiCanvas.height = window.innerHeight;
  };
  resizeCanvas();
  window.addEventListener("resize", resizeCanvas);
}

function launchConfetti(durationMs = 2200) {
  if (!confettiCtx || !confettiCanvas) {
    return;
  }

  const colors = ["#FF3333", "#E62B2B", "#00539F", "#2F74B4", "#FFAA22", "#FFC456"];
  const count = 130;
  const width = confettiCanvas.width;
  const height = confettiCanvas.height;
  const centerX = width / 2;

  confettiParticles = [];
  for (let i = 0; i < count; i += 1) {
    confettiParticles.push({
      x: centerX + (Math.random() - 0.5) * 120,
      y: height * 0.22 + (Math.random() - 0.5) * 24,
      vx: (Math.random() - 0.5) * 7,
      vy: -Math.random() * 7 - 3,
      gravity: 0.14 + Math.random() * 0.08,
      size: 4 + Math.random() * 6,
      rotation: Math.random() * Math.PI * 2,
      vr: (Math.random() - 0.5) * 0.35,
      color: colors[Math.floor(Math.random() * colors.length)],
      life: 0.8 + Math.random() * 0.8,
    });
  }

  const startTime = performance.now();
  if (confettiFrame) {
    cancelAnimationFrame(confettiFrame);
  }

  const tick = (ts) => {
    confettiCtx.clearRect(0, 0, confettiCanvas.width, confettiCanvas.height);
    const elapsed = ts - startTime;
    const keepRunning = elapsed < durationMs || confettiParticles.length > 0;

    confettiParticles = confettiParticles.filter((p) => p.life > 0);
    confettiParticles.forEach((p) => {
      p.vy += p.gravity;
      p.x += p.vx;
      p.y += p.vy;
      p.rotation += p.vr;
      p.life -= 0.013;

      confettiCtx.save();
      confettiCtx.translate(p.x, p.y);
      confettiCtx.rotate(p.rotation);
      confettiCtx.fillStyle = p.color;
      confettiCtx.fillRect(-p.size / 2, -p.size / 2, p.size, p.size * 0.65);
      confettiCtx.restore();
    });

    if (keepRunning) {
      confettiFrame = requestAnimationFrame(tick);
    } else {
      confettiCtx.clearRect(0, 0, confettiCanvas.width, confettiCanvas.height);
      confettiFrame = null;
    }
  };

  confettiFrame = requestAnimationFrame(tick);
}

function updateRemainingCount(value) {
  appState.remainingCount = value;
}

function getPrizeKey(prizeRank, prize) {
  return `${prizeRank}::${prize}`;
}

function getResultsForPrize(prize) {
  if (!prize) {
    return [];
  }

  const targetKey = getPrizeKey(prize.prize_rank, prize.prize);
  return appState.results.filter(
    (row) => getPrizeKey(row.prize_rank, row.prize) === targetKey
  );
}

function formatWinnerName(name) {
  return `🎉 ${name} 🎉`;
}

function updateHeaders() {
  const selected = getSelectedPrize();
  if (!selected) {
    resultsHeader.textContent = "- (0 winners)";
    return;
  }

  const prizeLabel = `${selected.prize_rank} - ${selected.prize}`;
  const winnerLabel = Number(selected.winner_num) === 1 ? "winner" : "winners";
  resultsHeader.textContent = `${prizeLabel} (${selected.winner_num} ${winnerLabel})`;
}

function appendResultRow(participant, rotating = false, reveal = false) {
  const tr = document.createElement("tr");
  if (rotating) {
    tr.classList.add("rotating");
  }
  if (reveal) {
    tr.classList.add("winner-reveal");
  }

  const td = document.createElement("td");
  td.textContent = participant;
  tr.appendChild(td);
  resultsBody.appendChild(tr);
  return tr;
}

function renderDisplayedPrizeResults() {
  resultsBody.innerHTML = "";
  const selectedPrize = getSelectedPrize();
  const rows = getResultsForPrize(selectedPrize);
  rows.forEach((row) => {
    const baseName = row.redraw ? `${row.participant} (R)` : row.participant;
    appendResultRow(formatWinnerName(baseName), false);
  });
  updateHeaders();
  setControlsEnabled(appState.prizes.length > 0);
}

function withBusyState(fn) {
  return async (...args) => {
    if (appState.isBusy) {
      return;
    }
    appState.isBusy = true;
    setControlsEnabled(appState.prizes.length > 0);
    try {
      await fn(...args);
    } finally {
      appState.isBusy = false;
      setControlsEnabled(appState.prizes.length > 0);
    }
  };
}

async function refreshState() {
  const response = await fetch("/api/state");
  const data = await response.json();

  appState.prizes = data.prizes || [];
  appState.results = data.results || [];
  appState.csvPath = data.csv_path || null;
  updateRemainingCount(data.remaining_count || 0);

  if (!response.ok || !data.ok) {
    appState.currentPrizeIndex = -1;
    setControlsEnabled(false);
    renderDisplayedPrizeResults();
    setStatus(actionStatus, data.message || "Could not load backend workbooks", true);
    return;
  }

  if (appState.prizes.length === 0) {
    appState.currentPrizeIndex = -1;
    setControlsEnabled(false);
    renderDisplayedPrizeResults();
    return;
  }

  if (appState.currentPrizeIndex < 0 || appState.currentPrizeIndex >= appState.prizes.length) {
    appState.currentPrizeIndex = appState.prizes.length - 1;
  }

  renderDisplayedPrizeResults();
}

function buildRollingName(pool) {
  if (!pool || pool.length === 0) {
    return "...";
  }
  const pointer = Math.floor(Math.random() * pool.length);
  return pool[pointer];
}

async function runPhasedRotation(renderTick) {
  const fastDurationMs = 3000;
  const slowDurationMs = 2000;
  const fastStepMs = 100;
  const slowStepMs = 200;
  let tick = 0;

  await new Promise((resolve) => {
    const fastInterval = setInterval(() => {
      tick += 1;
      renderTick(tick);
    }, fastStepMs);

    setTimeout(() => {
      clearInterval(fastInterval);

      const slowInterval = setInterval(() => {
        tick += 1;
        renderTick(tick);
      }, slowStepMs);

      setTimeout(() => {
        clearInterval(slowInterval);
        resolve();
      }, slowDurationMs);
    }, fastDurationMs);
  });
}

async function runDrawAnimation({ slots, animationPool, finalWinners }) {
  renderDisplayedPrizeResults();

  const rows = [];
  for (let i = 0; i < slots; i += 1) {
    rows.push(appendResultRow("...", true));
  }

  await runPhasedRotation((tick) => {
    rows.forEach((row, idx) => {
      row.children[0].textContent = buildRollingName(animationPool);
    });
  });

  rows.forEach((row, idx) => {
    const winner = finalWinners[idx];
    row.classList.remove("rotating");
    row.classList.add("winner-reveal");
    row.children[0].textContent = formatWinnerName(winner.display_name);
  });
  launchConfetti(2400);
}

async function runRedrawAnimation({ animationPool, finalWinner }) {
  renderDisplayedPrizeResults();

  const row = appendResultRow("...", true);

  await runPhasedRotation((tick) => {
    row.children[0].textContent = buildRollingName(animationPool);
  });

  row.classList.remove("rotating");
  row.classList.add("winner-reveal");
  row.children[0].textContent = formatWinnerName(finalWinner.display_name);
  launchConfetti(1800);
}

addParticipantButton.addEventListener(
  "click",
  withBusyState(async () => {
    setStatus(adhocStatus, "", false);
    const participant = adhocParticipantInput.value.trim();
    if (!participant) {
      setStatus(adhocStatus, "Please enter a participant name", true);
      return;
    }

    const response = await fetch("/api/participants/add", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ participant }),
    });

    const data = await response.json();
    if (!response.ok || !data.ok) {
      setStatus(adhocStatus, data.message || "Failed to add participant", true);
      return;
    }

    adhocParticipantInput.value = "";
    updateRemainingCount(data.remaining_count);
    setStatus(adhocStatus, "Added to the lucky pool!", false);
  })
);

adhocParticipantInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    if (!addParticipantButton.disabled) {
      addParticipantButton.click();
    }
  }
});

nextButton.addEventListener("click", () => {
  if (appState.isBusy || appState.currentPrizeIndex <= 0) {
    return;
  }
  setStatus(actionStatus, "", false);
  appState.currentPrizeIndex -= 1;
  renderDisplayedPrizeResults();
});

drawButton.addEventListener(
  "click",
  withBusyState(async () => {
    setStatus(actionStatus, "", false);

    const selectedPrize = getSelectedPrize();
    if (!selectedPrize) {
      setStatus(actionStatus, "No prize is available", true);
      return;
    }

    const drawCount = Number(selectedPrize.winner_num);
    if (!Number.isInteger(drawCount) || drawCount < 1) {
      setStatus(actionStatus, "winner_num in prize.xlsx must be an integer >= 1", true);
      return;
    }

    if (drawCount > appState.remainingCount) {
      setStatus(
        actionStatus,
        `Prize requires ${drawCount} winners, but only ${appState.remainingCount} participants remain`,
        true
      );
      return;
    }

    const response = await fetch("/api/draw", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        prize_id: selectedPrize.prize_id,
      }),
    });

    const data = await response.json();
    if (!response.ok || !data.ok) {
      setStatus(actionStatus, data.message || "Draw failed", true);
      return;
    }

    await runDrawAnimation({
      slots: drawCount,
      animationPool: data.animation_pool,
      finalWinners: data.final_winners,
    });

    data.final_winners.forEach((winner) => {
      appState.results.push({
        prize_rank: selectedPrize.prize_rank,
        prize: selectedPrize.prize,
        participant: winner.participant,
        group: winner.group,
        redraw: false,
      });
    });

    updateRemainingCount(data.remaining_count);
    updateHeaders();
  })
);

redrawButton.addEventListener(
  "click",
  withBusyState(async () => {
    setStatus(actionStatus, "", false);

    const selectedPrize = getSelectedPrize();
    if (!selectedPrize) {
      setStatus(actionStatus, "No prize is available", true);
      return;
    }

    if (appState.remainingCount < 1) {
      setStatus(actionStatus, "No participants remaining for redraw", true);
      return;
    }

    const response = await fetch("/api/redraw", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prize_id: selectedPrize.prize_id }),
    });

    const data = await response.json();
    if (!response.ok || !data.ok) {
      setStatus(actionStatus, data.message || "Redraw failed", true);
      return;
    }

    await runRedrawAnimation({
      animationPool: data.animation_pool,
      finalWinner: data.final_winner,
    });

    appState.results.push({
      prize_rank: selectedPrize.prize_rank,
      prize: selectedPrize.prize,
      participant: data.final_winner.participant,
      group: data.final_winner.group,
      redraw: true,
    });

    updateRemainingCount(data.remaining_count);
    updateHeaders();
  })
);

refreshState().catch(() => {
  setStatus(actionStatus, "Could not load the celebration state", true);
});

initConfettiCanvas();
