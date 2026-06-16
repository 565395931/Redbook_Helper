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

document.querySelectorAll(".draft-body").forEach(highlightExactSegments);

document.querySelectorAll("form").forEach((form) => {
  form.addEventListener("submit", () => {
    const button = form.querySelector("button[type='submit'][data-loading-text]");
    if (!button) return;
    button.dataset.originalText = button.textContent || "";
    button.textContent = button.dataset.loadingText || "Working...";
    button.disabled = true;
  });
});
