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
let viewerImages = [];
let viewerIndex = 0;

function showViewerImage(nextIndex) {
  if (!imageViewer || !viewerImage || !viewerImages.length) return;
  viewerIndex = (nextIndex + viewerImages.length) % viewerImages.length;
  viewerImage.src = viewerImages[viewerIndex];
  if (viewerCurrent) viewerCurrent.textContent = String(viewerIndex + 1);
  if (viewerTotal) viewerTotal.textContent = String(viewerImages.length);
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

imageViewer?.querySelector(".viewer-close")?.addEventListener("click", closeImageViewer);

imageViewer?.querySelectorAll(".viewer-nav").forEach((button) => {
  button.addEventListener("click", () => {
    showViewerImage(viewerIndex + Number(button.dataset.direction || 1));
  });
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
  form.addEventListener("submit", () => {
    const button = form.querySelector("button[type='submit'][data-loading-text]");
    if (!button) return;
    button.dataset.originalText = button.textContent || "";
    button.textContent = button.dataset.loadingText || "Working...";
    button.disabled = true;
  });
});
