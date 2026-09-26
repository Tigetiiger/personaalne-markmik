// Seaded: the sections other packages register (registerSettingsSection) and the password form.
import { api, listSettingsSections, toast } from "/static/app.js";
import { busy, h } from "./dom.js";
// Registers the Võistlused section; a static import, so the page preloads it with this module.
import "./voistlused.js";

export async function mount() {
  const host = document.getElementById("seaded-osad");
  const mounts = listSettingsSections().map((s) => {
    const body = h("div");
    host.append(h("section", { class: "plokk", id: `seaded-${s.id}` }, h("h2", {}, s.title), body));
    return Promise.resolve()
      .then(() => s.mount(body))
      .catch((err) => {
        console.error(`settings section ${s.id} failed`, err);
        body.textContent = "Selle osa laadimine ebaõnnestus.";
      });
  });

  const form = document.getElementById("parooli-vorm");
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const f = form.elements;
    if (f.new.value !== f.again.value) {
      toast("Uued paroolid ei klapi.", "error");
      return;
    }
    busy(form.querySelector("[type=submit]"), async () => {
      await api("/api/settings/password", { method: "POST", body: { old: f.old.value, new: f.new.value } });
      form.reset();
      toast("Parool muudetud.");
    });
  });
  await Promise.all(mounts);
}
