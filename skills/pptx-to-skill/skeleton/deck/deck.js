// Slide deck runtime: shows one slide at a time, scaled to the window.
// Keys: right/left arrows, space, Page Up/Down, Home/End; F for full screen. Clicking the
// left third of the window goes back, anywhere else goes forward. #3 in the URL opens slide 3.
// ?static in the URL shows every slide at full size instead (for screenshots).
(() => {
  if (new URLSearchParams(location.search).has("static")) {
    document.body.classList.add("static");
    return;
  }
  const slides = Array.from(document.querySelectorAll(".slide"));
  if (!slides.length) return;
  const root = document.documentElement;
  const size = (name, fallback) => parseFloat(getComputedStyle(root).getPropertyValue(name)) || fallback;
  let current = 0;

  function fit() {
    const scale = Math.min(innerWidth / size("--slide-width", 1920), innerHeight / size("--slide-height", 1080));
    root.style.setProperty("--deck-scale", scale);
  }

  function show(index) {
    current = Math.max(0, Math.min(slides.length - 1, index));
    slides.forEach((slide, i) => slide.classList.toggle("active", i === current));
    history.replaceState(null, "", location.href.split("#")[0] + "#" + (current + 1));
  }

  addEventListener("keydown", (event) => {
    if (["ArrowRight", "ArrowDown", "PageDown", " ", "Enter"].includes(event.key)) show(current + 1);
    else if (["ArrowLeft", "ArrowUp", "PageUp", "Backspace"].includes(event.key)) show(current - 1);
    else if (event.key === "Home") show(0);
    else if (event.key === "End") show(slides.length - 1);
    else if (event.key === "f") root.requestFullscreen?.();
    else return;
    event.preventDefault();
  });
  addEventListener("click", (event) => {
    if (event.target.closest("a")) return;
    show(current + (event.clientX < innerWidth / 3 ? -1 : 1));
  });
  addEventListener("hashchange", () => show((parseInt(location.hash.slice(1), 10) || 1) - 1));
  addEventListener("resize", fit);
  fit();
  show((parseInt(location.hash.slice(1), 10) || 1) - 1);
})();
