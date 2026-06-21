function escapeRegExp(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function highlightExactSegments(element) {
  let segments = [];
  try {
    segments = JSON.parse(element.dataset.segments || "[]");
  } catch {
    segments = [];
  }
  if (!segments.length) return;

  let html = element.textContent;
  for (const segment of segments) {
    if (!segment) continue;
    const pattern = new RegExp(escapeRegExp(segment), "g");
    html = html.replace(pattern, `<mark>${segment}</mark>`);
  }
  element.innerHTML = html;
}

function escapeHtml(value) {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function renderTopicTags(element) {
  if (element.querySelector(".topic-tag")) return;
  const original = element.textContent || "";
  const html = escapeHtml(original).replace(
    /#([^#\n\r]+?)\[话题\]#/g,
    (_, topic) => `<span class="topic-tag">${escapeHtml(topic.trim())}</span>`
  );
  element.innerHTML = html.replace(/#/g, "").replace(/\[话题\]/g, "");
}

document.querySelectorAll(".draft-body").forEach(highlightExactSegments);
document.querySelectorAll(".note-body").forEach(renderTopicTags);

document.querySelectorAll(".image-carousel").forEach((carousel) => {
  const slides = Array.from(carousel.querySelectorAll(".carousel-slide"));
  const counter = carousel.querySelector(".carousel-counter b");

  function showSlide(nextIndex) {
    const index = (nextIndex + slides.length) % slides.length;
    carousel.dataset.index = String(index);
    slides.forEach((slide, slideIndex) => {
      slide.classList.toggle("is-active", slideIndex === index);
    });
    if (counter) counter.textContent = String(index + 1);
  }

  slides.forEach((slide, slideIndex) => {
    slide.addEventListener("click", (event) => {
      event.preventDefault();
      openImageViewer(slides.map((item) => item.dataset.previewUrl || item.href), slideIndex);
    });
  });

  if (slides.length <= 1) return;

  carousel.querySelectorAll(".carousel-btn").forEach((button) => {
    button.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      const direction = Number(button.dataset.direction || 1);
      const currentIndex = Number(carousel.dataset.index || 0);
      showSlide(currentIndex + direction);
    });
  });
});

const imageViewer = document.querySelector("#imageViewer");
const viewerImage = imageViewer?.querySelector(".viewer-frame img");
const viewerCurrent = imageViewer?.querySelector(".viewer-frame figcaption b");
const viewerTotal = imageViewer?.querySelector(".viewer-frame figcaption span");
const viewerFrame = imageViewer?.querySelector(".viewer-frame");
const viewerNavButtons = Array.from(imageViewer?.querySelectorAll(".viewer-nav") || []);
let viewerImages = [];
let viewerIndex = 0;

function showViewerImage(nextIndex) {
  if (!imageViewer || !viewerImage || !viewerImages.length) return;
  viewerIndex = (nextIndex + viewerImages.length) % viewerImages.length;
  viewerImage.src = viewerImages[viewerIndex];
  if (viewerCurrent) viewerCurrent.textContent = String(viewerIndex + 1);
  if (viewerTotal) viewerTotal.textContent = String(viewerImages.length);
  viewerNavButtons.forEach((button) => {
    button.hidden = viewerImages.length <= 1;
  });
}

function openImageViewer(images, startIndex = 0) {
  if (!imageViewer || !viewerImage || !images.length) return;
  viewerImages = images;
  imageViewer.classList.add("is-open");
  imageViewer.setAttribute("aria-hidden", "false");
  document.body.style.overflow = "hidden";
  showViewerImage(startIndex);
}

function closeImageViewer() {
  if (!imageViewer || !viewerImage) return;
  imageViewer.classList.remove("is-open");
  imageViewer.setAttribute("aria-hidden", "true");
  document.body.style.overflow = "";
  viewerImage.src = "";
  viewerImages = [];
}

imageViewer?.querySelector(".viewer-close")?.addEventListener("click", (event) => {
  event.preventDefault();
  event.stopPropagation();
  closeImageViewer();
});

viewerNavButtons.forEach((button) => {
  button.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    showViewerImage(viewerIndex + Number(button.dataset.direction || 1));
  });
});

viewerFrame?.addEventListener("click", (event) => {
  event.stopPropagation();
});

imageViewer?.addEventListener("click", (event) => {
  if (event.target === imageViewer) closeImageViewer();
});

document.addEventListener("keydown", (event) => {
  if (!imageViewer?.classList.contains("is-open")) return;
  if (event.key === "Escape") closeImageViewer();
  if (event.key === "ArrowLeft") showViewerImage(viewerIndex - 1);
  if (event.key === "ArrowRight") showViewerImage(viewerIndex + 1);
});

document.querySelectorAll("form").forEach((form) => {
  form.addEventListener("submit", (event) => {
    const confirmation = form.dataset.confirm;
    if (confirmation && !window.confirm(confirmation)) {
      event.preventDefault();
      return;
    }

    const button = form.querySelector("button[type='submit'][data-loading-text]");
    if (!button) return;
    button.dataset.originalHtml = button.innerHTML;
    button.innerHTML = `<span class="button-spinner" aria-hidden="true"></span><span>${button.dataset.loadingText || "处理中..."}</span>`;
    button.classList.add("is-loading");
    button.disabled = true;
    form.setAttribute("aria-busy", "true");
  });
});

function removeQueryParams(keys) {
  if (!keys.length) return;
  const url = new URL(window.location.href);
  let changed = false;
  keys.forEach((key) => {
    if (url.searchParams.has(key)) {
      url.searchParams.delete(key);
      changed = true;
    }
  });
  if (!changed) return;
  const nextUrl = `${url.pathname}${url.search}${url.hash}`;
  window.history.replaceState({}, "", nextUrl);
}

document.querySelectorAll("[data-auto-dismiss]").forEach((alert) => {
  const queryKey = alert.dataset.removeQuery;
  if (queryKey) removeQueryParams([queryKey]);
  const timeout = Number(alert.dataset.autoDismiss || 5000);
  window.setTimeout(() => {
    alert.remove();
  }, timeout);
});

removeQueryParams(["copy_id", "show_latest", "image_id", "show_latest_image", "task_id"]);

const copywritingForm = document.querySelector("[data-copywriting-form]");
const copywritingResultPanel = document.querySelector("[data-copywriting-result-panel]");
const copywritingLoading = document.querySelector("[data-copywriting-loading]");
const copywritingStatus = document.querySelector("[data-copywriting-status]");
const hasCopywritingResult = Boolean(copywritingResultPanel?.querySelector(".copywriting-result"));
const hasCopywritingError = new URL(window.location.href).searchParams.has("copywriting_error");

function setCopywritingStatus(state) {
  if (!copywritingStatus) return;
  copywritingStatus.classList.remove("is-idle", "is-loading", "is-success", "is-error");
  copywritingStatus.classList.add(`is-${state}`);
  const label = copywritingStatus.dataset[`${state}Label`];
  const labelNode = copywritingStatus.querySelector("span:last-child");
  if (labelNode && label) {
    labelNode.textContent = label;
  }
}

if (hasCopywritingError) {
  setCopywritingStatus("error");
} else if (hasCopywritingResult) {
  setCopywritingStatus("success");
} else {
  setCopywritingStatus("idle");
}

const autoGenerateCopywriting = new URL(window.location.href).searchParams.get("auto_generate") === "1";
if (autoGenerateCopywriting && copywritingForm) {
  removeQueryParams(["auto_generate"]);
  window.setTimeout(() => {
    copywritingForm.requestSubmit();
  }, 60);
}

copywritingForm?.addEventListener("submit", () => {
  if (!copywritingResultPanel || !copywritingLoading) return;
  copywritingResultPanel.classList.add("is-generating");
  copywritingLoading.hidden = false;
  setCopywritingStatus("loading");
});

async function copyText(value) {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(value);
    return;
  }
  const helper = document.createElement("textarea");
  helper.value = value;
  helper.setAttribute("readonly", "true");
  helper.style.position = "fixed";
  helper.style.left = "-9999px";
  document.body.appendChild(helper);
  helper.select();
  document.execCommand("copy");
  helper.remove();
}

document.querySelectorAll("[data-copy-target]").forEach((button) => {
  button.addEventListener("click", async () => {
    const target = document.getElementById(button.dataset.copyTarget || "");
    if (!target) return;

    let text = target.value || target.textContent || "";
    if (button.dataset.copyTarget === "copywritingTitle") {
      const selectedTitle = document.querySelector(".title-options input[type='radio']:checked");
      if (selectedTitle) {
        text = selectedTitle.value;
      }
    }
    if (button.dataset.copyTarget === "copywritingPayload") {
      const selectedTitle = document.querySelector(".title-options input[type='radio']:checked");
      if (selectedTitle) {
        text = text.replace(/^标题：.*$/m, `标题：${selectedTitle.value}`);
      }
    }

    const originalText = button.textContent;
    try {
      await copyText(text.trim());
      button.textContent = "已复制";
    } catch {
      button.textContent = "复制失败";
    }
    window.setTimeout(() => {
      button.textContent = originalText;
    }, 1200);
  });
});

const imageForm = document.querySelector("[data-image-form]");
const imageFormPanel = document.querySelector(".images-form-panel");
const imageResultPanel = document.querySelector("[data-image-result-panel]");
const imageUrlField = document.querySelector("#imageUrlField");
const imageLabelField = document.querySelector("#imageLabelField");
const imageModeField = document.querySelector("#imageModeField");
const imagePromptField = document.querySelector("#imagePromptField");
const imageRemixPromptField = document.querySelector("#imageRemixPromptField");
const imageStageBadge = document.querySelector("#imageStageBadge");
const imageTaskPrompt = document.querySelector("#imageTaskPrompt");
const imageTaskInfo = document.querySelector("#imageTaskInfo");
const imageStageTarget = document.querySelector("#imageStageTarget");
const imageStageSpec = document.querySelector("#imageStageSpec");
const imageStatusDot = document.querySelector("[data-image-status-dot]");
const imageLoading = document.querySelector("[data-image-loading]");
const imageErrorMark = document.querySelector("[data-image-error-mark]");
const imageEmptyState = document.querySelector("[data-image-empty-state]");
const imagePreviewButton = document.querySelector("[data-image-preview-button]");
const imagePreview = document.querySelector("#imageResultPreview");
let imageTaskButtons = Array.from(document.querySelectorAll("[data-image-task]"));
let imageCountdownTimer = null;
let localImagePreviewItems = [];

function cssEscape(value) {
  if (window.CSS?.escape) return CSS.escape(String(value));
  return String(value).replace(/["\\]/g, "\\$&");
}

function setImageStatus(state) {
  if (!imageStatusDot) return;
  imageStatusDot.classList.remove("is-idle", "is-loading", "is-success", "is-error");
  imageStatusDot.classList.add(`is-${state}`);
  const titleMap = {
    idle: "等待任务",
    loading: "任务生成中",
    success: "任务已完成",
    error: "任务生成失败",
  };
  imageStatusDot.title = titleMap[state] || titleMap.idle;
}

function updateGlobalImageStatus() {
  imageTaskButtons = Array.from(document.querySelectorAll("[data-image-task]"));
  if (!imageTaskButtons.length) {
    setImageStatus("idle");
    return;
  }
  if (imageTaskButtons.some((button) => button.dataset.taskStatus === "error")) {
    setImageStatus("error");
    return;
  }
  if (imageTaskButtons.some((button) => button.dataset.taskStatus === "loading")) {
    setImageStatus("loading");
    return;
  }
  if (imageTaskButtons.every((button) => button.dataset.taskStatus === "success")) {
    setImageStatus("success");
    return;
  }
  setImageStatus("idle");
}

function syncImageSourceInput() {
  if (!imageForm || !imageUrlField) return;
  const activeInput = imageForm.querySelector("[data-image-panel]:not(.is-hidden) [data-image-source-input]");
  if (activeInput) {
    imageUrlField.value = activeInput.value?.trim() || "";
  } else {
    syncRemoteReferenceUrl();
  }
}

function syncImageLabel() {
  if (!imageLabelField || !imageModeField) return;
  const activeModeButton = imageFormPanel?.querySelector(".segment-button.is-active[data-image-form-mode]");
  const mode = activeModeButton?.dataset.imageFormMode || "prompt";
  imageModeField.value = mode;
  imageLabelField.value = mode === "remix" ? "图生图结果" : "文生图结果";
}

function clearImageCountdown() {
  if (imageCountdownTimer) {
    window.clearInterval(imageCountdownTimer);
    imageCountdownTimer = null;
  }
}

function startImageCountdown(seconds) {
  clearImageCountdown();
  const loadingSubtitle = imageLoading?.querySelector("small");
  if (!loadingSubtitle) return;
  let remaining = Number.isFinite(Number(seconds)) ? Math.max(0, Number(seconds)) : 120;
  const render = () => {
    loadingSubtitle.textContent = `预计还需要 ${remaining} 秒`;
  };
  render();
  imageCountdownTimer = window.setInterval(() => {
    remaining = Math.max(0, remaining - 1);
    render();
    if (remaining <= 0) clearImageCountdown();
  }, 1000);
}

function renderImageTask(button) {
  const mode = button?.dataset.taskMode || imageModeField?.value || "prompt";

  if (!button) {
    if (imageTaskPrompt) {
      imageTaskPrompt.textContent = "正在等待任务";
      imageTaskPrompt.title = "";
    }
    if (imageTaskInfo) {
      imageTaskInfo.textContent = "请在左侧填写信息并开始生成图像";
    }
    if (imageStageBadge) {
      imageStageBadge.textContent = mode === "remix" ? "图生图结果" : "文生图结果";
    }
    if (imageStageTarget) {
      imageStageTarget.textContent = "--";
    }
    if (imageStageSpec) {
      imageStageSpec.textContent = "图像规格 -- 分辨率 --";
    }
    if (imageEmptyState) imageEmptyState.hidden = false;
    if (imageLoading) imageLoading.hidden = true;
    if (imagePreviewButton) {
      imagePreviewButton.hidden = true;
      imagePreviewButton.dataset.previewSrc = "";
    }
    if (imagePreview) {
      imagePreview.removeAttribute("src");
      imagePreview.alt = "";
    }
    clearImageCountdown();
    setImageStatus("idle");
    return;
  }

  const prompt = button.dataset.taskPrompt || "";
  const target = button.dataset.taskTarget || "--";
  const ratio = button.dataset.taskRatio || "--";
  const resolution = button.dataset.taskResolution || "--";
  const model = button.dataset.taskModel || "";
  const imageSrc = button.dataset.taskImage || "";
  const status = button.dataset.taskStatus || "idle";
  const backendStatus = button.dataset.taskBackendStatus || "";
  const error = button.dataset.taskError || "";
  const progress = button.dataset.taskProgress || "";
  const eta = button.dataset.taskEta || "120";

  clearImageCountdown();
  if (imagePreview && imagePreviewButton) {
    imagePreview.removeAttribute("src");
    imagePreview.alt = "";
    imagePreviewButton.dataset.previewSrc = "";
  }
  if (imageErrorMark) imageErrorMark.hidden = true;
  imageLoading?.classList.remove("is-error");

  if (imageTaskPrompt) {
    imageTaskPrompt.textContent = `Prompt: ${prompt}`;
    imageTaskPrompt.title = prompt;
  }
  if (imageTaskInfo) {
    const taskInfoText = `输出风格：${target} | 图像规格：${ratio} | 分辨率：${resolution}${model ? ` | 模型：${model}` : ""}`;
    imageTaskInfo.textContent = taskInfoText;
    imageTaskInfo.title = taskInfoText;
  }
  if (imageStageBadge) {
    imageStageBadge.textContent = mode === "remix" ? "图生图结果" : "文生图结果";
  }
  if (imageStageTarget) {
    imageStageTarget.textContent = target;
  }
  if (imageStageSpec) {
    imageStageSpec.textContent = `图像规格 ${ratio} 分辨率 ${resolution}`;
  }
  if (imageEmptyState) imageEmptyState.hidden = true;

  if (imageSrc && imagePreview && imagePreviewButton) {
    imagePreview.src = imageSrc;
    imagePreview.alt = prompt;
    imagePreviewButton.dataset.previewSrc = imageSrc;
    imagePreviewButton.hidden = false;
    if (imageLoading) imageLoading.hidden = true;
  } else if (status === "error" && imagePreviewButton && imageLoading) {
    imagePreviewButton.hidden = true;
    imagePreviewButton.dataset.previewSrc = "";
    imageLoading.hidden = false;
    imageLoading.classList.add("is-error");
    if (imageErrorMark) imageErrorMark.hidden = false;
    const loadingTitle = imageLoading.querySelector("[data-image-loading-title]");
    const loadingSubtitle = imageLoading.querySelector("small");
    if (loadingTitle) loadingTitle.textContent = "生成失败";
    if (loadingSubtitle) loadingSubtitle.textContent = error || "请稍后重试";
  } else if (imagePreviewButton && imageLoading) {
    imagePreviewButton.hidden = true;
    imagePreviewButton.dataset.previewSrc = "";
    imageLoading.hidden = false;
    imageLoading.classList.remove("is-error");
    if (imageErrorMark) imageErrorMark.hidden = true;
    const loadingTitle = imageLoading.querySelector("[data-image-loading-title]");
    const loadingSubtitle = imageLoading.querySelector("small");
    if (loadingTitle) loadingTitle.textContent = "正在生成中...";
    if (loadingSubtitle && backendStatus === "uploading") {
      loadingSubtitle.textContent = "正在上传参考图到 KIE";
    } else if (loadingSubtitle && backendStatus === "creating") {
      loadingSubtitle.textContent = "正在创建 KIE 生图任务";
    } else if (loadingSubtitle && ["waiting", "generating", "queued"].includes(backendStatus)) {
      loadingSubtitle.textContent = "正在生成";
    } else if (loadingSubtitle && progress) {
      loadingSubtitle.textContent = `正在生成 · 进度 ${progress}%`;
    } else {
      startImageCountdown(eta);
    }
  }

  updateGlobalImageStatus();
}

function activateImageFormMode(mode) {
  document.querySelectorAll("[data-image-form-mode]").forEach((item) => {
    item.classList.toggle("is-active", item.dataset.imageFormMode === mode);
  });
  document.querySelectorAll("[data-image-panel]").forEach((panel) => {
    panel.classList.toggle("is-hidden", panel.dataset.imagePanel !== mode);
  });

  if (imagePromptField) {
    imagePromptField.required = mode === "prompt";
  }
  if (imageRemixPromptField) {
    imageRemixPromptField.required = mode === "remix";
  }

  syncImageLabel();
  syncImageSourceInput();
}

function activateImageResultMode(mode, renderFirstTask = true) {
  document.querySelectorAll("[data-image-result-mode]").forEach((item) => {
    item.classList.toggle("is-active", item.dataset.imageResultMode === mode);
  });
  document.querySelectorAll("[data-task-group]").forEach((group) => {
    group.classList.toggle("is-hidden", group.dataset.taskGroup !== mode);
  });

  const selectedButton = imageTaskButtons.find((item) => item.dataset.taskMode === mode && item.classList.contains("is-active"));
  const firstButton = selectedButton || imageTaskButtons.find((item) => item.dataset.taskMode === mode);
  document.querySelectorAll("[data-image-task]").forEach((item) => {
    item.classList.toggle("is-active", item === firstButton);
  });
  if (renderFirstTask) renderImageTask(firstButton || null);
  updateGlobalImageStatus();
}

function renderPreview(targetId, value, fallbackLabel) {
  const target = document.getElementById(targetId);
  if (!target) return;
  const url = (value || "").trim();
  if (!url) {
    target.classList.remove("has-image");
    target.innerHTML = escapeHtml(fallbackLabel);
    return;
  }
  target.classList.add("has-image");
  target.innerHTML = `<img src="${escapeHtml(url)}" alt="${escapeHtml(fallbackLabel)}" referrerpolicy="no-referrer">`;
}

document.querySelectorAll("[data-preview-target]").forEach((input) => {
  const fallbackLabel = input.dataset.previewLabel || "图片预览";
  const handler = () => {
    if (input.type === "file") return;
    renderPreview(input.dataset.previewTarget, input.value, fallbackLabel);
    if (input.hasAttribute("data-image-source-input")) {
      syncImageSourceInput();
    }
  };
  input.addEventListener("input", handler);
  input.addEventListener("change", handler);
});

function revokeLocalImagePreviewUrls() {
  localImagePreviewItems.forEach((item) => {
    if (item.source !== "remote") URL.revokeObjectURL(item.url);
  });
  localImagePreviewItems = [];
}

function syncLocalImageInputFiles(input) {
  if (!input || !window.DataTransfer) return;
  const transfer = new DataTransfer();
  localImagePreviewItems.forEach((item) => {
    if (item.file) transfer.items.add(item.file);
  });
  input.files = transfer.files;
}

function syncRemoteReferenceUrl() {
  if (!imageUrlField) return;
  const remoteItem = localImagePreviewItems.find((item) => item.source === "remote" && item.url);
  imageUrlField.value = remoteItem?.url || "";
}

function renderLocalImageCarousel(target, fallbackLabel, input) {
  if (!target) return;
  if (!localImagePreviewItems.length) {
    target.classList.remove("has-image");
    target.innerHTML = `
      <div class="reference-preview-empty">
        <strong>${escapeHtml(fallbackLabel)}</strong>
        <small>上传后会在这里预览，最多 4 张自动并排展示。</small>
      </div>
    `;
    return;
  }

  const count = Math.min(localImagePreviewItems.length, 4);
  target.classList.add("has-image");
  target.innerHTML = `
    <div class="reference-preview-grid" data-count="${count}" style="--reference-count: ${count}">
      ${localImagePreviewItems.slice(0, 4).map((item, index) => `
        <button type="button" class="reference-preview-item" data-reference-image="${index}" aria-label="查看参考图 ${index + 1}">
          <img src="${escapeHtml(item.url)}" alt="${escapeHtml(item.name || `参考图 ${index + 1}`)}">
          <span class="reference-preview-filename">${escapeHtml(item.name || `参考图 ${index + 1}`)}</span>
          <span class="reference-preview-remove" data-reference-remove="${index}" aria-label="删除参考图 ${index + 1}" title="删除">×</span>
        </button>
      `).join("")}
    </div>
  `;

  target.querySelectorAll("[data-reference-remove]").forEach((button) => {
    button.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      const index = Number(button.dataset.referenceRemove || 0);
      const removed = localImagePreviewItems.splice(index, 1)[0];
      if (removed?.url && removed.source !== "remote") URL.revokeObjectURL(removed.url);
      syncLocalImageInputFiles(input);
      syncRemoteReferenceUrl();
      renderLocalImageCarousel(target, fallbackLabel, input);
    });
  });

  target.querySelectorAll("[data-reference-image]").forEach((slide) => {
    slide.addEventListener("click", () => {
      const index = Number(slide.dataset.referenceImage || 0);
      openImageViewer(localImagePreviewItems.map((item) => item.url), index);
    });
  });
}

function setRemoteReferencePreview(url, name = "图1｜封面复刻") {
  const target = document.getElementById("referencePreview");
  const input = imageForm?.querySelector("[data-local-image-input]");
  if (!target || !url) return;
  revokeLocalImagePreviewUrls();
  localImagePreviewItems = [{
    file: null,
    name,
    source: "remote",
    url,
  }];
  syncLocalImageInputFiles(input);
  syncRemoteReferenceUrl();
  renderLocalImageCarousel(target, "参考图预览区", input);
}

document.querySelectorAll("[data-local-image-input]").forEach((input) => {
  const fallbackLabel = input.dataset.previewLabel || "参考图预览区";
  const previewId = input.dataset.previewTarget || "";
  const handler = () => {
    const target = document.getElementById(previewId);
    if (!target) return;
    const remainingSlots = Math.max(0, 4 - localImagePreviewItems.length);
    const files = Array.from(input.files || []).filter((file) => file.type.startsWith("image/")).slice(0, remainingSlots);
    const nextItems = files.map((file) => ({
      file,
      name: file.name || fallbackLabel,
      source: "local",
      url: URL.createObjectURL(file),
    }));
    localImagePreviewItems = [...localImagePreviewItems, ...nextItems].slice(0, 4);
    syncLocalImageInputFiles(input);
    syncRemoteReferenceUrl();
    renderLocalImageCarousel(target, fallbackLabel, input);
  };
  input.addEventListener("change", handler);
});

function resetSubmitButton(button) {
  if (!button) return;
  if (button.dataset.originalHtml) {
    button.innerHTML = button.dataset.originalHtml;
    delete button.dataset.originalHtml;
  }
  button.classList.remove("is-loading");
  button.disabled = false;
  imageForm?.removeAttribute("aria-busy");
}

function imageTaskButtonHtml(task) {
  const mode = escapeHtml(String(task.mode || "prompt"));
  const id = escapeHtml(String(task.id || ""));
  const rawId = String(task.id || "");
  const isPending = Boolean(task.is_pending || rawId.startsWith("pending-"));
  const prompt = escapeHtml(String(task.prompt || ""));
  const finalPrompt = escapeHtml(String(task.final_prompt || task.prompt || ""));
  const target = escapeHtml(String(task.output_target || ""));
  const ratio = escapeHtml(String(task.aspect_ratio || ""));
  const resolution = escapeHtml(String(task.resolution || ""));
  const model = escapeHtml(String(task.image_model_label || ""));
  const image = escapeHtml(String(task.image_url || ""));
  const progress = task.progress === null || task.progress === undefined ? "" : escapeHtml(String(task.progress));
  const error = escapeHtml(String(task.error || ""));
  const eta = task.eta_seconds === null || task.eta_seconds === undefined ? "" : escapeHtml(String(task.eta_seconds));
  const status = escapeHtml(String(task.status || "loading"));
  const backendStatus = escapeHtml(String(task.backend_status || ""));
  const label = escapeHtml(String(task.tab_label || "任务"));
  return `
    <span class="image-task-pill">
      <button
        type="button"
        class="image-task-tab is-active"
        data-image-task
        ${isPending ? 'data-temp-task="true"' : ""}
        data-task-mode="${mode}"
        data-task-id="${id}"
        data-task-status="${status}"
        data-task-backend-status="${backendStatus}"
        data-task-prompt="${prompt}"
        data-task-final-prompt="${finalPrompt}"
        data-task-target="${target}"
        data-task-ratio="${ratio}"
        data-task-resolution="${resolution}"
        data-task-model="${model}"
        data-task-image="${image}"
        data-task-progress="${progress}"
        data-task-error="${error}"
        data-task-eta="${eta}"
        title="${finalPrompt || prompt}"
      >${label}</button>
      <form method="post" action="${isPending ? "#" : `/image-tasks/${id}/delete`}" class="image-task-delete-form">
        <button type="submit" class="image-task-delete" aria-label="删除 ${label}" title="删除任务">×</button>
      </form>
    </span>
  `;
}

function ensureTaskTabs(mode) {
  const group = document.querySelector(`[data-task-group="${mode}"]`);
  if (!group) return null;
  let tabs = group.querySelector(".image-task-tabs");
  if (!tabs) {
    tabs = document.createElement("div");
    tabs.className = "image-task-tabs";
    group.appendChild(tabs);
  }
  return tabs;
}

function appendImageTask(task) {
  const mode = task?.mode || imageModeField?.value || "prompt";
  const tabs = ensureTaskTabs(mode);
  if (!tabs) return null;
  document.querySelectorAll("[data-image-task]").forEach((item) => item.classList.remove("is-active"));
  tabs.insertAdjacentHTML("beforeend", imageTaskButtonHtml(task));
  imageTaskButtons = Array.from(document.querySelectorAll("[data-image-task]"));
  const button = tabs.querySelector(`[data-task-id="${cssEscape(task.id)}"]`);
  activateImageResultMode(mode, false);
  if (button) {
    button.classList.add("is-active");
    renderImageTask(button);
  }
  return button;
}

function removeImageTaskById(taskId) {
  const button = document.querySelector(`[data-image-task][data-task-id="${cssEscape(taskId)}"]`);
  button?.closest(".image-task-pill")?.remove();
  imageTaskButtons = Array.from(document.querySelectorAll("[data-image-task]"));
}

function buildPendingImageTask(mode) {
  const target = imageForm?.querySelector("[name='output_target']")?.value || "--";
  const ratio = imageForm?.querySelector("[name='aspect_ratio']")?.value || "--";
  const resolution = imageForm?.querySelector("[name='resolution']")?.value || "--";
  const model = imageForm?.querySelector("[name='image_model'] option:checked")?.textContent || "";
  const promptValue = mode === "remix"
    ? imageRemixPromptField?.value.trim()
    : imagePromptField?.value.trim();
  return {
    id: `pending-${Date.now()}`,
    mode,
    tab_label: "提交中",
    prompt: promptValue || "正在生成",
    final_prompt: promptValue || "正在生成",
    output_target: target,
    aspect_ratio: ratio,
    resolution,
    image_model_label: model,
    image_url: "",
    status: "loading",
    backend_status: mode === "remix" ? "uploading" : "creating",
    progress: "",
    error: "",
    eta_seconds: 120,
    is_pending: true,
  };
}

document.querySelectorAll("[data-image-form-mode]").forEach((button) => {
  button.addEventListener("click", () => {
    activateImageFormMode(button.dataset.imageFormMode || "prompt");
  });
});

document.querySelectorAll("[data-image-result-mode]").forEach((button) => {
  button.addEventListener("click", () => {
    activateImageResultMode(button.dataset.imageResultMode || "prompt");
  });
});

document.addEventListener("click", (event) => {
  const button = event.target.closest?.("[data-image-task]");
  if (!button) return;
  document.querySelectorAll("[data-image-task]").forEach((item) => {
    item.classList.toggle("is-active", item === button);
  });
  renderImageTask(button);
});

document.addEventListener("submit", async (event) => {
  const form = event.target.closest?.(".image-task-delete-form");
  if (!form) return;
  event.preventDefault();
  const pill = form.closest(".image-task-pill");
  const deletedButton = pill?.querySelector("[data-image-task]");
  const mode = deletedButton?.dataset.taskMode || document.querySelector("[data-image-result-mode].is-active")?.dataset.imageResultMode || "prompt";
  const wasActive = Boolean(deletedButton?.classList.contains("is-active"));
  const deleteButton = form.querySelector("button[type='submit']");
  if (deleteButton) deleteButton.disabled = true;
  if (deletedButton?.dataset.tempTask === "true") {
    pill?.remove();
    imageTaskButtons = Array.from(document.querySelectorAll("[data-image-task]"));
    if (wasActive) {
      const nextButton = imageTaskButtons.find((item) => item.dataset.taskMode === mode);
      document.querySelectorAll("[data-image-task]").forEach((item) => {
        item.classList.toggle("is-active", item === nextButton);
      });
      renderImageTask(nextButton || null);
    }
    updateGlobalImageStatus();
    return;
  }
  try {
    const response = await fetch(form.action, {
      method: "POST",
      headers: { Accept: "application/json" },
    });
    if (!response.ok) throw new Error("删除任务失败");
    pill?.remove();
    imageTaskButtons = Array.from(document.querySelectorAll("[data-image-task]"));
    if (wasActive) {
      const nextButton = imageTaskButtons.find((item) => item.dataset.taskMode === mode);
      document.querySelectorAll("[data-image-task]").forEach((item) => {
        item.classList.toggle("is-active", item === nextButton);
      });
      renderImageTask(nextButton || null);
    }
    updateGlobalImageStatus();
  } catch {
    if (deleteButton) deleteButton.disabled = false;
  }
});

syncImageSourceInput();
syncImageLabel();
const initiallySelectedImageTask = imageTaskButtons.find((item) => item.classList.contains("is-active"));
const imagePageParams = new URL(window.location.href).searchParams;
const initialImageFormMode = imagePageParams.get("image_form_mode") === "remix" ? "remix" : "prompt";
activateImageFormMode(initialImageFormMode);
activateImageResultMode(initiallySelectedImageTask?.dataset.taskMode || "prompt");
const initialReferenceImageUrl = imagePageParams.get("reference_image_url") || "";
if (initialImageFormMode === "remix" && initialReferenceImageUrl) {
  setRemoteReferencePreview(initialReferenceImageUrl, imagePageParams.get("reference_image_name") || "图1｜封面复刻");
  removeQueryParams(["image_form_mode", "reference_image_url", "reference_image_name"]);
}

imageForm?.addEventListener("submit", async (event) => {
  syncImageSourceInput();
  syncImageLabel();
  const activeMode = imageModeField?.value || "prompt";
  const submitButton = event.submitter || imageForm.querySelector("button[type='submit'][data-loading-text]");

  if (activeMode === "prompt" && !imagePromptField?.value.trim()) {
    event.preventDefault();
    resetSubmitButton(submitButton);
    setImageStatus("idle");
    return;
  }
  if (activeMode === "remix" && !imageUrlField?.value.trim()) {
    const localFile = imageForm?.querySelector("[data-local-image-input]")?.files?.[0];
    if (!localFile && !localImagePreviewItems.length) {
      event.preventDefault();
      resetSubmitButton(submitButton);
      setImageStatus("idle");
      return;
    }
  }

  event.preventDefault();
  if (!imageResultPanel || !imageLoading || !imagePreviewButton) return;
  const pendingTask = buildPendingImageTask(activeMode);
  const pendingButton = appendImageTask(pendingTask);
  if (imageEmptyState) imageEmptyState.hidden = true;
  imagePreviewButton.hidden = true;
  imagePreviewButton.dataset.previewSrc = "";
  if (imagePreview) {
    imagePreview.removeAttribute("src");
    imagePreview.alt = "";
  }
  imageLoading.hidden = false;
  const loadingTitle = imageLoading.querySelector("[data-image-loading-title]");
  if (loadingTitle) loadingTitle.textContent = "正在生成中...";
  if (imageErrorMark) imageErrorMark.hidden = true;
  imageLoading.classList.remove("is-error");
  startImageCountdown(120);
  if (imageTaskPrompt && activeMode === "prompt") {
    const nextPrompt = imagePromptField?.value.trim() || "正在生成";
    imageTaskPrompt.textContent = `Prompt: ${nextPrompt}`;
    imageTaskPrompt.title = nextPrompt;
  } else if (imageTaskPrompt && activeMode === "remix") {
    const nextPrompt = imageRemixPromptField?.value.trim() || "正在生成";
    imageTaskPrompt.textContent = `Prompt: ${nextPrompt}`;
    imageTaskPrompt.title = nextPrompt;
  }
  if (imageTaskInfo) {
    const target = imageForm?.querySelector("[name='output_target']")?.value || "--";
    const ratio = imageForm?.querySelector("[name='aspect_ratio']")?.value || "--";
    const resolution = imageForm?.querySelector("[name='resolution']")?.value || "--";
    const model = imageForm?.querySelector("[name='image_model'] option:checked")?.textContent || "";
    const taskInfoText = `输出风格：${target} | 图像规格：${ratio} | 分辨率：${resolution}${model ? ` | 模型：${model}` : ""}`;
    imageTaskInfo.textContent = taskInfoText;
    imageTaskInfo.title = taskInfoText;
  }
  setImageStatus("loading");

  try {
    const response = await fetch(imageForm.action, {
      method: "POST",
      body: new FormData(imageForm),
      headers: { Accept: "application/json" },
    });
    let payload = {};
    try {
      payload = await response.json();
    } catch {
      payload = {};
    }
    if (!response.ok || payload.ok === false) {
      throw new Error(payload.detail || payload.error || payload.notice || "图片任务提交失败");
    }
    if (payload.task) {
      removeImageTaskById(pendingTask.id);
      appendImageTask(payload.task);
      setImageStatus("loading");
    }
    resetSubmitButton(submitButton);
  } catch (error) {
    resetSubmitButton(submitButton);
    if (imageLoading) {
      imageLoading.hidden = false;
      imageLoading.classList.add("is-error");
    }
    if (imageErrorMark) imageErrorMark.hidden = false;
    const loadingTitle = imageLoading?.querySelector("[data-image-loading-title]");
    const loadingSubtitle = imageLoading?.querySelector("small");
    if (loadingTitle) loadingTitle.textContent = "提交失败";
    if (loadingSubtitle) loadingSubtitle.textContent = error?.message || "请稍后重试";
    if (pendingButton) {
      pendingButton.dataset.taskStatus = "error";
      pendingButton.dataset.taskError = error?.message || "图片任务提交失败";
      pendingButton.textContent = "提交失败";
      renderImageTask(pendingButton);
    }
    setImageStatus("error");
  }
});

function applyImageTaskSnapshot(tasks) {
  if (!Array.isArray(tasks)) return false;
  let changedActiveTask = false;
  const byId = new Map(tasks.map((task) => [String(task.id), task]));
  imageTaskButtons = Array.from(document.querySelectorAll("[data-image-task]"));
  imageTaskButtons.forEach((button) => {
    const task = byId.get(String(button.dataset.taskId || ""));
    if (!task) return;
    const nextStatus = String(task.status || "idle");
    const nextBackendStatus = String(task.backend_status || "");
    const nextImage = String(task.image_url || "");
    const nextError = String(task.error || "");
    const nextProgress = task.progress === null || task.progress === undefined ? "" : String(task.progress);
    const nextEta = task.eta_seconds === null || task.eta_seconds === undefined ? "" : String(task.eta_seconds);
    const nextFinalPrompt = String(task.final_prompt || task.prompt || "");
    if (
      button.dataset.taskStatus !== nextStatus ||
      button.dataset.taskBackendStatus !== nextBackendStatus ||
      button.dataset.taskImage !== nextImage ||
      button.dataset.taskError !== nextError ||
      button.dataset.taskProgress !== nextProgress ||
      button.dataset.taskEta !== nextEta ||
      button.dataset.taskFinalPrompt !== nextFinalPrompt
    ) {
      button.dataset.taskStatus = nextStatus;
      button.dataset.taskBackendStatus = nextBackendStatus;
      button.dataset.taskImage = nextImage;
      button.dataset.taskError = nextError;
      button.dataset.taskProgress = nextProgress;
      button.dataset.taskEta = nextEta;
      button.dataset.taskFinalPrompt = nextFinalPrompt;
      button.title = nextFinalPrompt;
      changedActiveTask = changedActiveTask || button.classList.contains("is-active");
    }
  });
  tasks.forEach((task) => {
    const taskId = String(task.id || "");
    if (!taskId || document.querySelector(`[data-image-task][data-task-id="${cssEscape(taskId)}"]`)) return;
    document.querySelectorAll(`[data-image-task][data-temp-task="true"][data-task-mode="${cssEscape(task.mode || "prompt")}"]`).forEach((button) => {
      button.closest(".image-task-pill")?.remove();
    });
    appendImageTask(task);
    changedActiveTask = true;
  });
  updateGlobalImageStatus();
  return changedActiveTask;
}

async function refreshImageTasks() {
  if (!imageResultPanel) return;
  imageTaskButtons = Array.from(document.querySelectorAll("[data-image-task]"));
  if (imageTaskButtons.length && !imageTaskButtons.some((button) => button.dataset.taskStatus === "loading")) return;
  try {
    const response = await fetch("/image-tasks", { headers: { Accept: "application/json" } });
    if (!response.ok) return;
    const snapshot = await response.json();
    const tasks = [...(snapshot.prompt || []), ...(snapshot.remix || [])];
    if (applyImageTaskSnapshot(tasks)) {
      const activeButton = imageTaskButtons.find((button) => button.classList.contains("is-active"));
      renderImageTask(activeButton || null);
    }
  } catch {
    // Keep the current loading state; the backend poller continues independently.
  }
}

if (imageResultPanel) {
  window.setInterval(refreshImageTasks, 3000);
  refreshImageTasks();
}

imagePreviewButton?.addEventListener("click", () => {
  const src = imagePreviewButton.dataset.previewSrc;
  if (!src) return;
  openImageViewer([src], 0);
});

async function copyImageFromElement(element) {
  const src = element?.getAttribute("src");
  if (!src) throw new Error("missing image source");
  if (!navigator.clipboard?.write || !window.ClipboardItem) {
    throw new Error("clipboard image write is not supported");
  }
  let blob = null;
  try {
    const response = await fetch(src, { mode: "cors" });
    if (!response.ok) throw new Error("image fetch failed");
    blob = await response.blob();
  } catch {
    const response = await fetch(`/image-proxy?url=${encodeURIComponent(src)}`);
    if (!response.ok) throw new Error("image proxy fetch failed");
    blob = await response.blob();
  }
  const pngBlob = await convertBlobToPng(blob);
  await navigator.clipboard.write([new ClipboardItem({ "image/png": pngBlob })]);
  return "image";
}

async function convertBlobToPng(blob) {
  if (blob.type === "image/png") return blob;
  const bitmap = await createImageBitmap(blob);
  const canvas = document.createElement("canvas");
  canvas.width = bitmap.width;
  canvas.height = bitmap.height;
  const context = canvas.getContext("2d");
  if (!context) throw new Error("canvas is not available");
  context.drawImage(bitmap, 0, 0);
  bitmap.close?.();
  return await new Promise((resolve, reject) => {
    canvas.toBlob((nextBlob) => {
      if (nextBlob) resolve(nextBlob);
      else reject(new Error("image conversion failed"));
    }, "image/png");
  });
}

document.querySelectorAll("[data-copy-image-target]").forEach((button) => {
  button.addEventListener("click", async () => {
    const target = document.getElementById(button.dataset.copyImageTarget || "");
    if (!target) return;
    const originalText = button.textContent;
    try {
      await copyImageFromElement(target);
      button.textContent = "已复制图片";
    } catch {
      button.textContent = "复制失败";
    }
    window.setTimeout(() => {
      button.textContent = originalText;
    }, 1200);
  });
});

if (document.querySelector("[data-scoring-note]")) {
  window.setTimeout(() => {
    window.location.reload();
  }, 3000);
}

const remixModal = document.getElementById("remixModal");
const remixNoteIdInput = document.getElementById("remixNoteId");
const remixNoteTitle = document.getElementById("remixModalNoteTitle");

function openRemixModal(noteId, title) {
  if (!remixModal || !remixNoteIdInput) return;
  remixNoteIdInput.value = noteId || "";
  if (remixNoteTitle) {
    remixNoteTitle.textContent = `参考内容：${title || "未命名笔记"}`;
  }
  remixModal.classList.add("is-open");
  remixModal.setAttribute("aria-hidden", "false");
  document.body.style.overflow = "hidden";
}

window.openRemixModalFromButton = function openRemixModalFromButton(button) {
  if (!button) return;
  openRemixModal(button.dataset.noteId || "", button.dataset.noteTitle || "");
};

function closeRemixModal() {
  if (!remixModal) return;
  remixModal.classList.remove("is-open");
  remixModal.setAttribute("aria-hidden", "true");
  document.body.style.overflow = "";
}

document.querySelectorAll("[data-open-remix-modal]").forEach((button) => {
  button.addEventListener("click", () => {
    openRemixModal(button.dataset.noteId || "", button.dataset.noteTitle || "");
  });
});

document.querySelectorAll("[data-close-remix-modal]").forEach((button) => {
  button.addEventListener("click", closeRemixModal);
});

remixModal?.addEventListener("click", (event) => {
  if (event.target === remixModal) closeRemixModal();
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && remixModal?.classList.contains("is-open")) {
    closeRemixModal();
  }
});

const videoMigrationRoot = document.querySelector("[data-video-migration]");
const videoStepOneForm = document.querySelector("[data-video-step-one-form]");
const videoTaskList = document.querySelector("[data-video-task-list]");
const videoEmptyTask = document.querySelector("[data-video-empty-task]");
const videoGlobalStatus = document.querySelector("[data-video-global-status]");
const videoStepTwoForm = document.querySelector("[data-video-step-two-form]");
const videoBackHomeButton = document.querySelector("[data-video-back-home]");
const videoPageTitle = document.querySelector("[data-video-page-title]");
const videoPageSubtitle = document.querySelector("[data-video-page-subtitle]");
const videoStepTwoImage = document.querySelector("[data-video-step-two-image]");
const videoStepTwoCaption = document.querySelector("[data-video-step-two-caption]");
const videoStepTwoLoading = document.querySelector("[data-video-step-two-loading]");
const videoStepTwoLoadingText = videoStepTwoLoading?.querySelector("p");
const videoPreviewUrls = new Map();

function getVideoTargetDropzone() {
  return videoStepTwoForm?.querySelector(".video-migration-video-dropzone") || null;
}

function clearTargetVideoPreview() {
  const preview = videoStepTwoForm?.querySelector('[data-video-preview="target-video"]');
  const previousVideoPreview = videoPreviewUrls.get("video-target-video");
  if (previousVideoPreview) URL.revokeObjectURL(previousVideoPreview);
  videoPreviewUrls.delete("video-target-video");
  if (preview) preview.innerHTML = "";
}

function setTargetVideoLoading(isLoading, message = "") {
  const dropzone = getVideoTargetDropzone();
  dropzone?.classList.toggle("is-generating", Boolean(isLoading));
  if (videoStepTwoLoading) videoStepTwoLoading.hidden = !isLoading;
  if (videoStepTwoLoadingText && message) {
    videoStepTwoLoadingText.textContent = message;
  }
}

function showTargetResultVideo(resultVideo) {
  const preview = videoStepTwoForm?.querySelector('[data-video-preview="target-video"]');
  const dropzone = getVideoTargetDropzone();
  if (!preview || !resultVideo) return;
  const existingVideo = preview.querySelector("video");
  if (existingVideo?.dataset.resultUrl === resultVideo) {
    dropzone?.classList.add("has-file", "has-result");
    videoStepTwoForm?.classList.add("has-video-result");
    return;
  }
  clearTargetVideoPreview();
  preview.innerHTML = `<video src="${escapeHtml(resultVideo)}" controls playsinline></video>`;
  const nextVideo = preview.querySelector("video");
  if (nextVideo) nextVideo.dataset.resultUrl = resultVideo;
  dropzone?.classList.add("has-file", "has-result");
  videoStepTwoForm?.classList.add("has-video-result");
  const label = videoStepTwoForm?.querySelector('[data-video-file-name="target-video"]');
  if (label) label.textContent = "生成结果视频";
}

function setVideoMigrationGlobalStatus(state) {
  if (!videoGlobalStatus) return;
  videoGlobalStatus.classList.remove("is-idle", "is-loading", "is-success", "is-error");
  videoGlobalStatus.classList.add(`is-${state}`);
  const titleMap = {
    idle: "等待任务",
    loading: "有任务生成中",
    success: "任务已完成",
    error: "任务失败",
  };
  videoGlobalStatus.title = titleMap[state] || titleMap.idle;
}

function updateVideoMigrationGlobalStatus() {
  if (!videoTaskList) return;
  const cards = Array.from(videoTaskList.querySelectorAll("[data-video-task-card]"));
  if (!cards.length) {
    setVideoMigrationGlobalStatus("idle");
    return;
  }
  if (cards.some((card) => card.dataset.taskStatus === "processing")) {
    setVideoMigrationGlobalStatus("loading");
    return;
  }
  if (cards.some((card) => card.dataset.taskStatus === "error")) {
    setVideoMigrationGlobalStatus("error");
    return;
  }
  setVideoMigrationGlobalStatus("success");
}

function hasProcessingVideoMigrationTask() {
  if (!videoTaskList) return false;
  return Array.from(videoTaskList.querySelectorAll("[data-video-task-card]")).some((card) => card.dataset.taskStatus === "processing");
}

function normalizeVideoMigrationStatus(status) {
  if (status === "success" || status === "done") return "done";
  if (status === "error") return "error";
  return "processing";
}

function videoMigrationPlaceholderIcon() {
  return `
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <rect x="6" y="3" width="12" height="18" rx="2"></rect>
      <path d="M9 8h6"></path>
      <path d="M9 12h6"></path>
      <path d="M10 17h4"></path>
    </svg>
  `;
}

function renderVideoMigrationTask(task) {
  const normalizedStatus = normalizeVideoMigrationStatus(task.status);
  const isDone = normalizedStatus === "done";
  const isError = normalizedStatus === "error";
  const videoStatus = task.video_status || "idle";
  const normalizedVideoStatus = normalizeVideoMigrationStatus(videoStatus);
  const isVideoRunning = videoStatus === "loading";
  const isVideoDone = normalizedVideoStatus === "done";
  const isVideoError = normalizedVideoStatus === "error";
  const taskId = escapeHtml(String(task.id || ""));
  const displayIndex = escapeHtml(String(task.display_index || task.id || ""));
  const label = escapeHtml(String(task.tab_label || `任务 ${displayIndex}`));
  const sourceImage = String(task.source_image_url || "");
  const resultImage = String(task.result_image_url || "");
  const resultVideo = String(task.result_video_url || "");
  const thumbImage = resultImage || sourceImage;
  const error = escapeHtml(String(task.error || ""));
  const progress = task.progress === null || task.progress === undefined || task.progress === "" ? "" : ` · ${escapeHtml(String(task.progress))}%`;
  const etaSeconds = Number(task.eta_seconds || 0);
  const etaText = etaSeconds > 0 ? `，预计剩余 ${Math.ceil(etaSeconds / 60)} 分钟` : "";
  const cardStatus = isError || isVideoError ? "error" : isVideoRunning ? "processing" : isDone || isVideoDone ? "done" : "processing";
  const headline = isVideoDone
    ? "视频迁移已完成"
    : isVideoRunning
      ? `视频迁移中${etaText}`
      : isVideoError
        ? "视频迁移失败"
        : isDone
          ? "首帧已完成"
          : isError
            ? "首帧生成失败"
            : `首帧生成中${progress}`;
  const stateText = cardStatus === "done" ? "已完成" : cardStatus === "error" ? "失败" : "进行中";
  return `
    <article
      class="video-migration-task-card"
      data-video-task-card
      data-task-id="${taskId}"
      data-task-label="${label}"
      data-task-display-index="${displayIndex}"
      data-task-status="${cardStatus}"
      data-task-backend-status="${escapeHtml(String(task.backend_status || ""))}"
      data-task-video-status="${escapeHtml(String(videoStatus))}"
      data-task-runninghub-status="${escapeHtml(String(task.runninghub_status || ""))}"
      data-task-progress="${escapeHtml(String(task.progress || ""))}"
      data-task-error="${error}"
      data-task-result-image="${escapeHtml(resultImage)}"
      data-task-result-video="${escapeHtml(resultVideo)}"
      data-task-source-image="${escapeHtml(sourceImage)}"
    >
      <button type="button" class="video-migration-task-delete" data-video-delete-task="${taskId}" aria-label="删除 ${label}" title="删除任务">×</button>
      <div class="video-migration-task-top">
        <div class="video-migration-task-title">
          <h3>${label}</h3>
          <small>${headline}</small>
        </div>
        <div class="video-migration-task-state ${cardStatus === "done" ? "is-done" : cardStatus === "error" ? "is-error" : "is-processing"}">
          <i class="dot"></i>${stateText}
        </div>
      </div>
      <div class="video-migration-task-body">
        <div class="video-migration-task-thumb">
          ${thumbImage ? `<img src="${escapeHtml(thumbImage)}" alt="${label} 首帧图" referrerpolicy="no-referrer">` : videoMigrationPlaceholderIcon()}
        </div>
        <div class="video-migration-task-steps">
          <div class="video-migration-task-step">
            <span>1. 生成换脸首帧</span>
            <i class="video-migration-mini-light ${isDone ? "is-done" : isError ? "is-error" : "is-processing"}"></i>
          </div>
          <div class="video-migration-task-step">
            <span>2. ${isVideoDone ? "视频迁移完成" : isVideoRunning ? `视频迁移中${etaText}` : isVideoError ? "视频迁移失败" : isDone ? "可进入视频迁移" : isError ? "等待重新提交" : "等待进入视频迁移"}</span>
            <i class="video-migration-mini-light ${isVideoDone ? "is-done" : isVideoError ? "is-error" : isVideoRunning ? "is-processing" : ""}"></i>
          </div>
        </div>
      </div>
      ${error ? `<div class="video-migration-task-error">${error}</div>` : ""}
      <div class="video-migration-task-actions">
        ${
          isVideoDone && resultVideo
            ? `<a class="video-migration-download" href="${escapeHtml(resultVideo)}" target="_blank" rel="noreferrer" download>一键下载视频</a>`
            : isVideoRunning
              ? `<button type="button" class="secondary" disabled><span class="button-spinner" aria-hidden="true"></span><span>视频迁移中</span></button>`
              : isDone
                ? `<button type="button" data-video-enter-step-two="${taskId}">进入第二步</button>`
                : isError
              ? `<button type="button" class="secondary" disabled>生成失败</button>`
              : `<button type="button" class="secondary" disabled><span class="button-spinner" aria-hidden="true"></span><span>等待首帧完成</span></button>`
        }
      </div>
    </article>
  `;
}

function upsertVideoMigrationTask(task) {
  if (!videoTaskList) return;
  const taskId = String(task.id || "");
  const currentCard = videoTaskList.querySelector(`[data-video-task-card][data-task-id="${cssEscape(taskId)}"]`);
  const wasActive = currentCard?.classList.contains("is-active");
  if (currentCard) {
    currentCard.outerHTML = renderVideoMigrationTask(task);
  } else {
    if (videoEmptyTask && videoTaskList.contains(videoEmptyTask)) {
      videoEmptyTask.insertAdjacentHTML("beforebegin", renderVideoMigrationTask(task));
    } else {
      videoTaskList.insertAdjacentHTML("beforeend", renderVideoMigrationTask(task));
    }
  }
  const nextCard = videoTaskList.querySelector(`[data-video-task-card][data-task-id="${cssEscape(taskId)}"]`);
  if (nextCard) {
    nextCard.classList.toggle("is-active", Boolean(wasActive) || !videoTaskList.querySelector(".video-migration-task-card.is-active"));
  }
  if (videoStepTwoForm?.dataset.taskId === taskId && ["success", "done", "error"].includes(String(task.video_status || ""))) {
    setTargetVideoLoading(false);
    resetSubmitButton(videoStepTwoForm.querySelector("button[type='submit']"));
    if (task.result_video_url) {
      showTargetResultVideo(String(task.result_video_url || ""));
      if (videoStepTwoCaption) {
        videoStepTwoCaption.textContent = `${task.tab_label || `任务 ${task.display_index || task.id}`} 首帧图`;
      }
    }
  }
  if (videoEmptyTask) videoEmptyTask.hidden = Boolean(videoTaskList.querySelector("[data-video-task-card]"));
  updateVideoMigrationGlobalStatus();
}

document.querySelectorAll("[data-video-image-input]").forEach((input) => {
  input.addEventListener("change", () => {
    const role = input.dataset.videoImageInput || "";
    const label = document.querySelector(`[data-video-file-name="${cssEscape(role)}"]`);
    const preview = document.querySelector(`[data-video-preview="${cssEscape(role)}"]`);
    const file = input.files?.[0];
    input.closest(".video-migration-dropzone")?.classList.toggle("has-file", Boolean(file));
    if (label) label.textContent = file?.name || "未选择文件";
    const previousUrl = videoPreviewUrls.get(role);
    if (previousUrl) URL.revokeObjectURL(previousUrl);
    if (!preview) return;
    preview.innerHTML = "";
    if (file) {
      const nextUrl = URL.createObjectURL(file);
      videoPreviewUrls.set(role, nextUrl);
      preview.innerHTML = `<img src="${nextUrl}" alt="${escapeHtml(file.name)}">`;
    } else {
      videoPreviewUrls.delete(role);
    }
  });
});

document.querySelectorAll("[data-video-input]").forEach((input) => {
  input.addEventListener("change", () => {
    const role = input.dataset.videoInput || "";
    const label = document.querySelector(`[data-video-file-name="${cssEscape(role)}"]`);
    const preview = document.querySelector(`[data-video-preview="${cssEscape(role)}"]`);
    const file = input.files?.[0];
    const dropzone = input.closest(".video-migration-video-dropzone");
    dropzone?.classList.toggle("has-file", Boolean(file));
    dropzone?.classList.remove("has-result", "is-generating");
    videoStepTwoForm?.classList.remove("has-video-result");
    setTargetVideoLoading(false);
    if (label) label.textContent = file?.name || "未选择文件";
    const previewKey = `video-${role}`;
    const previousUrl = videoPreviewUrls.get(previewKey);
    if (previousUrl) URL.revokeObjectURL(previousUrl);
    if (!preview) return;
    preview.innerHTML = "";
    if (file) {
      const nextUrl = URL.createObjectURL(file);
      videoPreviewUrls.set(previewKey, nextUrl);
      preview.innerHTML = `<video src="${nextUrl}" muted playsinline preload="metadata"></video>`;
    } else {
      videoPreviewUrls.delete(previewKey);
    }
  });
});

videoStepOneForm?.addEventListener("submit", (event) => {
  event.preventDefault();
  if (!videoTaskList) return;

  const sourceInput = videoStepOneForm.querySelector('[data-video-image-input="source"]');
  const faceInput = videoStepOneForm.querySelector('[data-video-image-input="face"]');
  const sourceFile = sourceInput?.files?.[0];
  const faceFile = faceInput?.files?.[0];
  const submitButton = event.submitter || videoStepOneForm.querySelector("button[type='submit']");

  if (!sourceFile || !faceFile) {
    resetSubmitButton(submitButton);
    return;
  }

  const formData = new FormData(videoStepOneForm);
  setVideoMigrationGlobalStatus("loading");
  fetch(videoStepOneForm.action, {
    method: "POST",
    body: formData,
    headers: { Accept: "application/json" },
  })
    .then(async (response) => {
      let payload = {};
      try {
        payload = await response.json();
      } catch {
        payload = {};
      }
      if (!response.ok || payload.ok === false) {
        throw new Error(payload.error || payload.detail || "首帧任务提交失败");
      }
      if (payload.task) upsertVideoMigrationTask(payload.task);
      videoStepOneForm.reset();
      document.querySelectorAll("[data-video-file-name]").forEach((label) => {
        label.textContent = "未选择文件";
      });
      document.querySelectorAll("[data-video-preview]").forEach((preview) => {
        preview.innerHTML = "";
      });
      document.querySelectorAll(".video-migration-dropzone.has-file").forEach((dropzone) => {
        dropzone.classList.remove("has-file");
      });
      videoPreviewUrls.forEach((url) => URL.revokeObjectURL(url));
      videoPreviewUrls.clear();
    })
    .catch((error) => {
      const task = {
        id: `error-${Date.now()}`,
        tab_label: "提交失败",
        status: "error",
        source_image_url: "",
        result_image_url: "",
        error: error?.message || "首帧任务提交失败",
      };
      upsertVideoMigrationTask(task);
      setVideoMigrationGlobalStatus("error");
    })
    .finally(() => {
      resetSubmitButton(submitButton);
    });
});

function showVideoMigrationStepOne() {
  if (!videoStepOneForm || !videoStepTwoForm) return;
  videoStepOneForm.hidden = false;
  videoStepTwoForm.hidden = true;
  setTargetVideoLoading(false);
  if (videoBackHomeButton) videoBackHomeButton.hidden = true;
  if (videoPageTitle) videoPageTitle.textContent = "视频迁移";
  if (videoPageSubtitle) {
    videoPageSubtitle.textContent = "左侧创建新任务，右侧统一管理任务状态。每个任务互相隔离，完成首帧后从任务卡进入第二步。";
  }
}

function showVideoMigrationStepTwo(taskId) {
  if (!videoStepOneForm || !videoStepTwoForm || !videoTaskList) return;
  const taskCard = videoTaskList.querySelector(`[data-video-task-card][data-task-id="${cssEscape(taskId)}"]`);
  if (!taskCard) return;
  const taskLabel = taskCard.dataset.taskLabel || `任务 ${taskId}`;
  const resultImage = taskCard.dataset.taskResultImage || taskCard.dataset.taskSourceImage || "";
  const resultVideo = taskCard.dataset.taskResultVideo || "";
  const videoStatus = taskCard.dataset.taskVideoStatus || "idle";
  videoStepOneForm.hidden = true;
  videoStepTwoForm.hidden = false;
  setTargetVideoLoading(videoStatus === "loading", "任务正在 RunningHub 处理中，会继续在右侧独立更新。");
  videoStepTwoForm.dataset.taskId = taskId;
  if (videoBackHomeButton) videoBackHomeButton.hidden = false;
  if (videoPageTitle) videoPageTitle.textContent = `${taskLabel}：视频迁移`;
  if (videoPageSubtitle) {
    videoPageSubtitle.textContent = "生成的首帧图已自动带入左侧。上传目标视频后点击生成，当前任务会继续在右侧独立更新。";
  }
  if (videoStepTwoImage) videoStepTwoImage.hidden = false;
  if (videoStepTwoImage && resultImage) {
    videoStepTwoImage.src = resultImage;
  }
  if (videoStepTwoCaption) {
    videoStepTwoCaption.textContent = `${taskLabel} 首帧图`;
  }
  const videoInput = videoStepTwoForm.querySelector("[data-video-input]");
  if (videoInput) videoInput.value = "";
  const videoLabel = videoStepTwoForm.querySelector('[data-video-file-name="target-video"]');
  if (videoLabel) videoLabel.textContent = "未选择文件";
  clearTargetVideoPreview();
  const dropzone = getVideoTargetDropzone();
  dropzone?.classList.remove("has-file", "has-result");
  videoStepTwoForm.classList.remove("has-video-result");
  if (resultVideo) {
    showTargetResultVideo(resultVideo);
  }
}

videoMigrationRoot?.addEventListener("click", (event) => {
  const deleteButton = event.target.closest?.("[data-video-delete-task]");
  if (deleteButton) {
    event.preventDefault();
    event.stopPropagation();
    const taskId = deleteButton.dataset.videoDeleteTask || "";
    const card = deleteButton.closest("[data-video-task-card]");
    deleteVideoMigrationTask(taskId, card);
    return;
  }

  const button = event.target.closest?.("[data-video-enter-step-two]");
  if (button) {
    event.preventDefault();
    event.stopPropagation();
    showVideoMigrationStepTwo(button.dataset.videoEnterStepTwo || "");
    return;
  }

  const card = event.target.closest?.("[data-video-task-card]");
  if (!card || !videoTaskList?.contains(card)) return;
  videoTaskList.querySelectorAll("[data-video-task-card]").forEach((item) => {
    item.classList.toggle("is-active", item === card);
  });
  if (["loading", "success", "done", "error"].includes(card.dataset.taskVideoStatus || "")) {
    showVideoMigrationStepTwo(card.dataset.taskId || "");
  } else if (card.dataset.taskStatus !== "done") {
    showVideoMigrationStepOne();
  }
});

async function deleteVideoMigrationTask(taskId, card) {
  if (!taskId || !card) return;
  const deleteButton = card.querySelector("[data-video-delete-task]");
  if (deleteButton) deleteButton.disabled = true;
  try {
    const response = await fetch(`/video-migration/tasks/${encodeURIComponent(taskId)}/delete`, {
      method: "POST",
      headers: { Accept: "application/json" },
    });
    if (!response.ok) throw new Error("删除任务失败");
    const wasActive = card.classList.contains("is-active");
    const stepTwoTaskId = videoStepTwoForm?.dataset.taskId || "";
    card.remove();
    if (stepTwoTaskId === taskId) {
      showVideoMigrationStepOne();
    }
    if (wasActive && videoTaskList) {
      const nextCard = videoTaskList.querySelector("[data-video-task-card]");
      nextCard?.classList.add("is-active");
    }
    if (videoEmptyTask && videoTaskList) {
      videoEmptyTask.hidden = Boolean(videoTaskList.querySelector("[data-video-task-card]"));
    }
    updateVideoMigrationGlobalStatus();
  } catch {
    if (deleteButton) deleteButton.disabled = false;
  }
}

videoBackHomeButton?.addEventListener("click", () => {
  showVideoMigrationStepOne();
});

function getVideoDuration(file) {
  return new Promise((resolve) => {
    if (!file) {
      resolve(0);
      return;
    }
    const video = document.createElement("video");
    const url = URL.createObjectURL(file);
    const cleanup = () => URL.revokeObjectURL(url);
    video.preload = "metadata";
    video.onloadedmetadata = () => {
      const duration = Number.isFinite(video.duration) ? video.duration : 0;
      cleanup();
      resolve(duration);
    };
    video.onerror = () => {
      cleanup();
      resolve(0);
    };
    video.src = url;
  });
}

videoStepTwoForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const submitButton = event.submitter || videoStepTwoForm.querySelector("button[type='submit']");
  const videoInput = videoStepTwoForm.querySelector("[data-video-input]");
  const file = videoInput?.files?.[0];
  const taskId = videoStepTwoForm.dataset.taskId || "";
  if (!file || !taskId) return;
  const duration = await getVideoDuration(file);
  if (duration > 20.5) {
    alert("目标视频最长支持 20 秒。");
    return;
  }
  const estimateText = duration > 0 ? `预计约 ${Math.max(1, Math.ceil((duration * 90) / 60))} 分钟，任务会在右侧持续更新。` : "任务会在右侧持续更新。";
  if (submitButton) {
    submitButton.dataset.originalHtml = submitButton.innerHTML;
    submitButton.innerHTML = `<span class="button-spinner" aria-hidden="true"></span><span>生成中…</span>`;
    submitButton.classList.add("is-loading");
    submitButton.disabled = true;
  }
  setTargetVideoLoading(true, estimateText);
  try {
    const formData = new FormData();
    formData.append("task_id", taskId);
    formData.append("target_video", file);
    formData.append("video_duration_seconds", duration ? String(duration) : "");
    const response = await fetch("/video-migration/step2", {
      method: "POST",
      body: formData,
      headers: { Accept: "application/json" },
    });
    let payload = {};
    try {
      payload = await response.json();
    } catch {
      payload = {};
    }
    if (!response.ok || payload.ok === false) {
      throw new Error(payload.error || payload.detail || "视频迁移提交失败");
    }
    if (payload.task) upsertVideoMigrationTask(payload.task);
  } catch (error) {
    setTargetVideoLoading(false);
    resetSubmitButton(submitButton);
    alert(error?.message || "视频迁移提交失败");
  }
});

function applyVideoMigrationSnapshot(tasks) {
  if (!Array.isArray(tasks) || !videoTaskList) return;
  tasks.forEach(upsertVideoMigrationTask);
  if (videoEmptyTask) videoEmptyTask.hidden = tasks.length > 0;
  updateVideoMigrationGlobalStatus();
}

async function refreshVideoMigrationTasks() {
  if (!videoMigrationRoot) return;
  try {
    const response = await fetch("/video-migration/tasks", { headers: { Accept: "application/json" } });
    if (!response.ok) return;
    const snapshot = await response.json();
    applyVideoMigrationSnapshot(snapshot.tasks || []);
  } catch {
    // Keep the current task state; the next poll will retry.
  }
}

if (videoMigrationRoot) {
  if (videoTaskList?.querySelector("[data-video-task-card]") && videoEmptyTask) {
    videoEmptyTask.hidden = true;
  }
  updateVideoMigrationGlobalStatus();
  window.setInterval(() => {
    if (hasProcessingVideoMigrationTask()) {
      refreshVideoMigrationTasks();
    }
  }, 3000);
  window.setInterval(refreshVideoMigrationTasks, 30000);
  refreshVideoMigrationTasks();
}
