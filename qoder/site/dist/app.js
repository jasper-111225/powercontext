(function () {
  "use strict";

  var tabs = Array.prototype.slice.call(document.querySelectorAll(".tab"));
  var scenes = Array.prototype.slice.call(document.querySelectorAll(".scene"));

  function activate(name) {
    tabs.forEach(function (tab) {
      var active = tab.dataset.scene === name;
      tab.classList.toggle("is-active", active);
      tab.setAttribute("aria-selected", active ? "true" : "false");
      tab.tabIndex = active ? 0 : -1;
    });
    scenes.forEach(function (scene) {
      scene.classList.toggle("is-active", scene.id === "scene-" + name);
    });
  }

  tabs.forEach(function (tab, index) {
    tab.addEventListener("click", function () {
      activate(tab.dataset.scene);
    });
    tab.addEventListener("keydown", function (event) {
      if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
      event.preventDefault();
      var step = event.key === "ArrowRight" ? 1 : -1;
      var next = tabs[(index + step + tabs.length) % tabs.length];
      next.focus();
      activate(next.dataset.scene);
    });
  });

  var hotTimer = null;

  document.addEventListener("click", function (event) {
    var button = event.target.closest(".cite");
    if (!button) return;
    var scene = button.closest(".scene");
    if (!scene) return;
    var card = scene.querySelector('.ev[data-n="' + button.dataset.n + '"]');
    if (!card) return;

    scene.querySelectorAll(".ev.is-hot").forEach(function (el) {
      el.classList.remove("is-hot");
    });
    card.classList.add("is-hot");
    if (typeof card.scrollIntoView === "function") {
      card.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
    if (hotTimer) clearTimeout(hotTimer);
    hotTimer = setTimeout(function () {
      card.classList.remove("is-hot");
    }, 2400);
  });

  var first = tabs.filter(function (tab) {
    return tab.classList.contains("is-active");
  })[0];
  if (first) {
    activate(first.dataset.scene);
  }
})();
