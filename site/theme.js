// Theme before first paint (otherwise the light page flashes): saved choice, else the system
// setting. A blocking external script, because the CSP forbids inline ones. The browser's own bar
// takes the top bar's colour (--surface); app.js keeps it in step when the theme changes.
(function () {
  var t = null;
  try { t = localStorage.getItem("teema"); } catch (e) {}
  if (t !== "tume" && t !== "hele") t = matchMedia("(prefers-color-scheme: dark)").matches ? "tume" : "hele";
  document.documentElement.dataset.teema = t;
  var meta = document.getElementById("teema-varv");
  if (meta) meta.content = t === "tume" ? "#15181e" : "#ffffff";
})();
