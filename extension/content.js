/*
 * content.js — rulează ÎN pagină (injectat la click din popup). Scanează
 * câmpurile, le potrivește cu profilul prin ATSMatcher (matcher.js, injectat
 * înainte) și completează. NU trimite formularul — doar umple; submit-ul rămâne
 * acțiunea umană. Expune self.__atsFillPage(profile) → {filled, skipped}.
 */
(function () {
  "use strict";

  function labelFor(el) {
    // 1. aria-label direct.
    if (el.getAttribute("aria-label")) return el.getAttribute("aria-label");
    // 2. aria-labelledby → textul elementelor referite.
    const labelledby = el.getAttribute("aria-labelledby");
    if (labelledby) {
      const txt = labelledby
        .split(/\s+/)
        .map((id) => (document.getElementById(id) || {}).textContent || "")
        .join(" ")
        .trim();
      if (txt) return txt;
    }
    // 3. <label for="id">.
    if (el.id) {
      const lbl = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (lbl && lbl.textContent) return lbl.textContent;
    }
    // 4. <label> care înconjoară câmpul.
    const wrap = el.closest("label");
    if (wrap && wrap.textContent) return wrap.textContent;
    return "";
  }

  function setValue(el, value) {
    // Setter nativ ca React/Vue (care ascultă pe setterul prototipului) să
    // „vadă" schimbarea; un simplu el.value=... e adesea ignorat de framework-uri.
    const proto = el.tagName === "TEXTAREA"
      ? window.HTMLTextAreaElement.prototype
      : window.HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, "value");
    if (setter && setter.set) setter.set.call(el, value);
    else el.value = value;
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  }

  self.__atsFillPage = function (profile) {
    const matcher = self.ATSMatcher;
    if (!matcher) return { filled: 0, skipped: 0, error: "matcher lipsă" };
    profile = profile || {};
    let filled = 0;
    let skipped = 0;

    const fields = document.querySelectorAll("input, textarea");
    for (const el of fields) {
      if (el.disabled || el.readOnly || el.offsetParent === null) continue;
      if (el.value && el.value.trim()) continue; // nu suprascriem ce e completat
      const ctx = {
        autocomplete: el.getAttribute("autocomplete") || "",
        name: el.getAttribute("name") || "",
        id: el.id || "",
        placeholder: el.getAttribute("placeholder") || "",
        label: labelFor(el),
        type: el.getAttribute("type") || el.type || "text",
      };
      const key = matcher.matchField(ctx);
      if (!key) continue;
      const value = profile[key];
      if (value === undefined || value === null || value === "") {
        skipped += 1; // câmp recunoscut, dar profilul n-are valoare
        continue;
      }
      setValue(el, value);
      filled += 1;
    }
    return { filled, skipped };
  };
})();
