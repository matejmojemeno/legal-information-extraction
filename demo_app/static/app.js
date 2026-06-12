document.addEventListener("DOMContentLoaded", () => {
  let activeFilter = "all";
  let aiPollTimer = null;
  let analysisProgressTimer = null;
  let deterministicPollTimer = null;
  let deterministicAnimationTimer = null;

  const bindFilePicker = () => {
    const fileInput = document.getElementById("file");
    const fileName = document.getElementById("file-name");
    if (!fileInput || !fileName) return;

    const updateFileName = () => {
      const selected = fileInput.files?.[0];
      fileName.textContent = selected ? selected.name : "No file selected";
    };

    fileInput.onchange = updateFileName;
    updateFileName();
  };

  const applyFilter = (kind) => {
    activeFilter = kind;
    document.querySelectorAll(".filter-chip").forEach((button) => {
      button.classList.toggle("active", button.dataset.filter === kind);
    });

    document.querySelectorAll(".reference-card-wrap").forEach((node) => {
      const itemKind = node.dataset.kind;
      node.style.display = kind === "all" || itemKind === kind ? "" : "none";
    });

    document.querySelectorAll(".reference-mark").forEach((mark) => {
      const itemKind = mark.dataset.kind;
      mark.classList.toggle("is-hidden", !(kind === "all" || itemKind === kind));
    });

    syncSidebarGroups();
  };

  const bindFilterButtons = () => {
    document.querySelectorAll(".filter-chip").forEach((button) => {
      button.onclick = () => applyFilter(button.dataset.filter);
    });
  };

  const bindReferenceCards = () => {
    document.querySelectorAll(".reference-card[data-target-id]").forEach((card) => {
      card.onclick = () => {
        const targetId = card.dataset.targetId;
        if (!targetId) return;
        const target = document.getElementById(targetId);
        if (!target) return;
        target.scrollIntoView({ behavior: "smooth", block: "center" });
        target.animate(
          [
            { boxShadow: "0 0 0 0 rgba(21, 64, 93, 0.0)" },
            { boxShadow: "0 0 0 8px rgba(21, 64, 93, 0.18)" },
            { boxShadow: "0 0 0 0 rgba(21, 64, 93, 0.0)" },
          ],
          { duration: 900, easing: "ease-out" },
        );
      };
    });
  };

  const refreshInteractiveUi = () => {
    bindFilterButtons();
    bindReferenceCards();
    applyFilter(activeFilter);
  };

  const syncSidebarGroups = () => {
    const syncGroup = (groupId, kind) => {
      const group = document.getElementById(groupId);
      if (!group) return;
      const cards = Array.from(group.querySelectorAll(".reference-card-wrap"));
      const visibleCards = cards.filter((card) => card.style.display !== "none");
      group.hidden = visibleCards.length === 0;
    };

    syncGroup("law-reference-group", "law_reference");
    syncGroup("document-reference-group", "document_reference");
  };

  const updateAiFragments = (payload) => {
    const html = payload?.html || {};
    const stats = document.getElementById("stats-row-container");
    const aiStatus = document.getElementById("ai-status-container");
    const textPanel = document.getElementById("text-panel-container");
    const lawList = document.getElementById("law-reference-list-container");
    const documentList = document.getElementById("document-reference-list-container");
    if (stats && typeof html.stats === "string") stats.innerHTML = html.stats;
    if (aiStatus && typeof html.ai_status === "string") aiStatus.innerHTML = html.ai_status;
    if (textPanel && typeof html.text_panel === "string") textPanel.innerHTML = html.text_panel;
    if (lawList && typeof html.law_list === "string") lawList.innerHTML = html.law_list;
    if (documentList && typeof html.document_list === "string") documentList.innerHTML = html.document_list;
    refreshInteractiveUi();
  };

  const stopPolling = () => {
    if (aiPollTimer) {
      window.clearTimeout(aiPollTimer);
      aiPollTimer = null;
    }
  };

  const stopDeterministicPolling = () => {
    if (deterministicPollTimer) {
      window.clearTimeout(deterministicPollTimer);
      deterministicPollTimer = null;
    }
  };

  const stopDeterministicAnimation = () => {
    if (deterministicAnimationTimer) {
      window.clearInterval(deterministicAnimationTimer);
      deterministicAnimationTimer = null;
    }
  };

  const pollAiStatus = async (resultId) => {
    try {
      const response = await fetch(`/api/results/${encodeURIComponent(resultId)}/ai-status`, {
        headers: { Accept: "application/json" },
      });
      if (!response.ok) {
        stopPolling();
        return;
      }
      const payload = await response.json();
      updateAiFragments(payload);
      const status = payload?.ai_job?.status;
      if (status === "completed" || status === "failed" || status === "disabled" || !status) {
        stopPolling();
        return;
      }
      aiPollTimer = window.setTimeout(() => pollAiStatus(resultId), 2000);
    } catch (_error) {
      aiPollTimer = window.setTimeout(() => pollAiStatus(resultId), 4000);
    }
  };

  const bindDeterministicProgressPage = () => {
    const progressPanel = document.getElementById("analysis-progress-panel");
    const progressBar = document.getElementById("analysis-progress-bar");
    const progressLabel = document.getElementById("analysis-progress-label");
    const progressValue = document.getElementById("analysis-progress-value");
    const progressMessage = document.getElementById("analysis-progress-message");
    const resultId = progressPanel?.dataset.resultId;
    if (!progressPanel || !progressBar || !progressLabel || !progressValue || !progressMessage || !resultId) {
      return;
    }

    const phaseLabels = {
      queued: "Queued",
      extract_text: "Extracting text",
      extract_local_aliases: "Extracting local aliases",
      load_runtime_aliases: "Loading runtime aliases",
      extract_document_references: "Extracting decision references",
      extract_law_occurrences: "Extracting law references",
      derive_law_anomalies: "Reviewing law anomalies",
      link_document_references: "Linking decision references",
      build_document_ai_tasks: "Preparing decision fallbacks",
      load_document_metadata: "Loading local metadata",
      build_law_alias_index: "Building law alias index",
      build_result: "Building result view",
      done: "Done",
    };
    const phaseMessages = {
      queued: "The upload was received and is waiting to start SPP processing.",
      extract_text: "Reading the PDF and extracting searchable text.",
      extract_local_aliases: "Collecting aliases defined inside the uploaded decision.",
      load_runtime_aliases: "Loading the shared alias resources used by the SPP resolver.",
      extract_document_references: "Finding decision references in the extracted text.",
      extract_law_occurrences: "Finding and resolving law references in the extracted text.",
      derive_law_anomalies: "Separating clearly resolved law citations from harder anomaly cases.",
      link_document_references: "Checking decision references against the local corpus snapshot.",
      build_document_ai_tasks: "Preparing optional BRL tasks for genuinely hard decision references.",
      load_document_metadata: "Loading local document metadata for linked references.",
      build_law_alias_index: "Preparing the alias index used by the result view.",
      build_result: "Preparing the inline highlights, sidebar cards, and linked previews.",
      done: "The SPP result is ready. Opening the analysis view now.",
    };

    let displayedProgress = 5;

    const renderStatus = (job, { immediate = false } = {}) => {
      const targetProgress = Math.max(0, Math.min(100, Number(job?.progress || 0)));
      const phase = String(job?.phase || "queued");
      progressLabel.textContent = phaseLabels[phase] || phase;
      progressMessage.textContent = job?.error
        ? String(job.error)
        : (phaseMessages[phase] || "Deterministic analysis is still running.");

      if (immediate) {
        displayedProgress = targetProgress;
        progressBar.style.width = `${displayedProgress}%`;
        progressValue.textContent = `${Math.round(displayedProgress)}%`;
        return;
      }

      stopDeterministicAnimation();
      deterministicAnimationTimer = window.setInterval(() => {
        const remaining = targetProgress - displayedProgress;
        if (Math.abs(remaining) < 0.6) {
          displayedProgress = targetProgress;
          progressBar.style.width = `${displayedProgress}%`;
          progressValue.textContent = `${Math.round(displayedProgress)}%`;
          stopDeterministicAnimation();
          return;
        }

        displayedProgress += Math.max(0.8, Math.min(4.5, remaining * 0.28));
        if (displayedProgress > targetProgress) {
          displayedProgress = targetProgress;
        }
        progressBar.style.width = `${displayedProgress}%`;
        progressValue.textContent = `${Math.round(displayedProgress)}%`;
      }, 120);
    };

    const pollDeterministicStatus = async () => {
      try {
        const response = await fetch(`/api/results/${encodeURIComponent(resultId)}/status`, {
          headers: { Accept: "application/json" },
        });
        if (!response.ok) {
          stopDeterministicPolling();
          return;
        }
        const payload = await response.json();
        const job = payload?.deterministic_job || {};
        renderStatus(job);

        if (payload?.ready && typeof payload.redirect_url === "string") {
          stopDeterministicPolling();
          stopDeterministicAnimation();
          window.location.href = payload.redirect_url;
          return;
        }

        if (job?.status === "failed") {
          stopDeterministicPolling();
          stopDeterministicAnimation();
          return;
        }

        deterministicPollTimer = window.setTimeout(pollDeterministicStatus, 900);
      } catch (_error) {
        deterministicPollTimer = window.setTimeout(pollDeterministicStatus, 1500);
      }
    };

    renderStatus({ phase: "queued", progress: 5 }, { immediate: true });
    deterministicPollTimer = window.setTimeout(pollDeterministicStatus, 250);
  };

  const bindUploadProgress = () => {
    const form =
      document.getElementById("upload-form") ||
      document.querySelector("form[action='/analyze']") ||
      document.querySelector("form.upload-card");
    const submitButton =
      document.getElementById("analyze-button") ||
      form?.querySelector("button[type='submit']");
    const heroPanel = document.querySelector(".hero-panel");
    if (!form || !heroPanel || !submitButton) {
      return;
    }

    let progressPanel = document.getElementById("analysis-progress-panel");
    if (!progressPanel) {
      progressPanel = document.createElement("section");
      progressPanel.id = "analysis-progress-panel";
      progressPanel.className = "analysis-progress-panel";
      progressPanel.hidden = true;
      progressPanel.setAttribute("aria-live", "polite");
      progressPanel.innerHTML = `
        <div class="analysis-progress-copy">
          <p class="eyebrow">Deterministic Analysis</p>
          <h2>Preparing the reference view.</h2>
          <p class="hero-text" id="analysis-progress-message">
            Upload received. Starting deterministic extraction now.
          </p>
        </div>
        <div class="analysis-progress-card">
          <div class="analysis-progress-bar-shell" aria-hidden="true">
            <div class="analysis-progress-bar" id="analysis-progress-bar"></div>
          </div>
          <div class="analysis-progress-meta">
            <strong id="analysis-progress-label">Uploading PDF</strong>
            <span id="analysis-progress-value">8%</span>
          </div>
          <p class="hint-text">
            The bar tracks the deterministic stages of the upload flow. Final timing still depends on PDF extraction and text size.
          </p>
        </div>
      `;
      heroPanel.insertAdjacentElement("afterend", progressPanel);
    }

    const progressBar = document.getElementById("analysis-progress-bar");
    const progressLabel = document.getElementById("analysis-progress-label");
    const progressValue = document.getElementById("analysis-progress-value");
    const progressMessage = document.getElementById("analysis-progress-message");
    if (!progressBar || !progressLabel || !progressValue || !progressMessage) {
      return;
    }
    let submitArmed = false;

    const stages = [
      { percent: 8, label: "Uploading PDF", message: "Upload received. Starting SPP extraction now." },
      { percent: 26, label: "Extracting text", message: "Reading the PDF and converting it into searchable text." },
      { percent: 48, label: "Resolving law references", message: "Running the SPP law-reference extractor and resolver." },
      { percent: 68, label: "Linking decision references", message: "Checking extracted decision references against the local corpus snapshot." },
      { percent: 84, label: "Preparing the result view", message: "Building the inline highlights, sidebar cards, and local previews." },
      { percent: 94, label: "Finalizing response", message: "Finishing the SPP result and loading the analysis screen." },
    ];

    const renderStage = (progress) => {
      const stage = [...stages].reverse().find((item) => progress >= item.percent) || stages[0];
      progressBar.style.width = `${progress}%`;
      progressLabel.textContent = stage.label;
      progressValue.textContent = `${Math.round(progress)}%`;
      progressMessage.textContent = stage.message;
    };

    const waitForPaint = () =>
      new Promise((resolve) => {
        window.requestAnimationFrame(() => {
          window.requestAnimationFrame(resolve);
        });
      });

    const startProgressAndSubmit = async (event) => {
      if (submitArmed) {
        return;
      }
      event.preventDefault();
      const fileInput = document.getElementById("file");
      if (fileInput && !fileInput.files?.length) return;

      heroPanel.hidden = true;
      progressPanel.hidden = false;

      let progress = 8;
      renderStage(progress);
      window.scrollTo(0, 0);
      await waitForPaint();

      if (analysisProgressTimer) {
        window.clearInterval(analysisProgressTimer);
      }
      analysisProgressTimer = window.setInterval(() => {
        progress = Math.min(progress + (progress < 68 ? 6 : 3), 94);
        renderStage(progress);
      }, 900);

      try {
        submitArmed = true;
        HTMLFormElement.prototype.submit.call(form);
      } catch (_error) {
        submitArmed = false;
        if (analysisProgressTimer) {
          window.clearInterval(analysisProgressTimer);
          analysisProgressTimer = null;
        }
        progressMessage.textContent = "The analysis request failed unexpectedly. Please try the upload once more.";
        progressLabel.textContent = "Request failed";
        progressValue.textContent = "0%";
        progressBar.style.width = "0%";
        heroPanel.hidden = false;
        progressPanel.hidden = true;
      }
    };

    submitButton.addEventListener("click", startProgressAndSubmit);
    form.addEventListener("submit", startProgressAndSubmit);
  };

  refreshInteractiveUi();
  bindFilePicker();
  bindUploadProgress();
  bindDeterministicProgressPage();

  const aiStatusContainer = document.getElementById("ai-status-container");
  const resultId = aiStatusContainer?.dataset.resultId;
  const initialStatus = document.getElementById("result-layout")?.dataset.aiJobStatus;
  if (resultId && initialStatus && initialStatus !== "disabled" && initialStatus !== "completed" && initialStatus !== "failed") {
    aiPollTimer = window.setTimeout(() => pollAiStatus(resultId), 1200);
  }
});
