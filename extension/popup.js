/* popup.js — încarcă profilul, injectează matcher+content și completează pagina. */
"use strict";

const statusEl = document.getElementById("status");

function show(cls, msg) {
  statusEl.className = cls;
  statusEl.textContent = msg;
}

document.getElementById("opts").addEventListener("click", (e) => {
  e.preventDefault();
  chrome.runtime.openOptionsPage();
});

document.getElementById("fill").addEventListener("click", async () => {
  show("", "Fülle aus…");
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    const stored = await chrome.storage.local.get("ats_profile");
    const profile = stored.ats_profile || {};
    if (!Object.values(profile).some((v) => v && String(v).trim())) {
      show("warn", "Profil ist leer — zuerst unter „Profil bearbeiten“ ausfüllen.");
      return;
    }
    // matcher.js setează self.ATSMatcher; content.js setează self.__atsFillPage.
    await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      files: ["matcher.js", "content.js"],
    });
    const [res] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: (p) => self.__atsFillPage(p),
      args: [profile],
    });
    const r = (res && res.result) || { filled: 0, skipped: 0 };
    if (r.error) {
      show("err", "Fehler: " + r.error);
    } else if (r.filled === 0) {
      show("warn", `0 Felder gefüllt${r.skipped ? ` (${r.skipped} erkannt, aber Profil leer)` : ""}.`);
    } else {
      show("ok", `${r.filled} Feld(er) gefüllt. Bitte prüfen und selbst absenden.`);
    }
  } catch (e) {
    show("err", "Fehler: " + (e && e.message ? e.message : e));
  }
});
