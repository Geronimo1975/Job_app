/* options.js — randează formularul de profil și salvează în chrome.storage.local. */
"use strict";

const form = document.getElementById("form");

// Construiește câmpurile din PROFILE_FIELDS (profile.js).
for (const f of PROFILE_FIELDS) {
  const div = document.createElement("div");
  div.className = "field";
  const label = document.createElement("label");
  label.textContent = f.label;
  label.setAttribute("for", "f_" + f.key);
  const input = document.createElement("input");
  input.id = "f_" + f.key;
  input.dataset.key = f.key;
  div.append(label, input);
  form.append(div);
}

// Precompletează din storage.
chrome.storage.local.get(STORAGE_KEY).then((stored) => {
  const profile = stored[STORAGE_KEY] || {};
  for (const input of form.querySelectorAll("input")) {
    input.value = profile[input.dataset.key] || "";
  }
});

document.getElementById("save").addEventListener("click", async () => {
  const profile = {};
  for (const input of form.querySelectorAll("input")) {
    const v = input.value.trim();
    if (v) profile[input.dataset.key] = v;
  }
  await chrome.storage.local.set({ [STORAGE_KEY]: profile });
  const saved = document.getElementById("saved");
  saved.textContent = "✓ Gespeichert";
  setTimeout(() => (saved.textContent = ""), 2000);
});
