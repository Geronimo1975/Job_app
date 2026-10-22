/*
 * matcher.js — potrivirea câmp-formular → cheie de profil.
 *
 * Miezul extensiei, scris ca modul PUR ca să fie testabil în Node fără browser.
 * Nu atinge DOM-ul: primește un „context" de câmp (atributele deja extrase) și
 * întoarce cheia de profil (sau null). Extragerea din DOM și completarea stau în
 * content.js; aici e doar logica de decizie, cea mai predispusă la bug-uri.
 *
 * Ordinea contează: `autocomplete` (standard HTML, cel mai fiabil) bate euristica
 * pe label/name/placeholder. Prima regulă care se potrivește câștigă.
 */

(function (root) {
  "use strict";

  // autocomplete standard → cheie de profil. Cel mai sigur semnal.
  const AUTOCOMPLETE = {
    "given-name": "firstName",
    "additional-name": "firstName",
    "family-name": "lastName",
    name: "fullName",
    email: "email",
    tel: "phone",
    "tel-national": "phone",
    "street-address": "address",
    "address-line1": "address",
    "address-level2": "city",
    "postal-code": "zip",
    "country-name": "country",
    organization: "currentCompany",
    url: "website",
  };

  // Euristici pe text (label/name/id/placeholder), în ordine de prioritate.
  // Cheia e profilul; valoarea e un regex testat pe textul normalizat.
  const HEURISTICS = [
    ["email", /\b(e[-\s]?mail)\b/],
    // „telefon(nummer)?" prinde compusul german „Telefonnummer"; „linked in"
    // tolerează spargerea camelCase a lui „LinkedIn".
    ["phone", /\b(phone|telephone|mobile|handy|telefon(nummer)?|tel)\b/],
    ["linkedin", /\blinked[\s_-]?in\b/],
    ["github", /\bgithub\b/],
    ["website", /\b(website|portfolio|webseite|homepage)\b/],
    // „first/last" înaintea genericului „name", altfel „name" le-ar fura.
    ["firstName", /\b(first[\s_-]?name|vorname|given[\s_-]?name|forename)\b/],
    ["lastName", /\b(last[\s_-]?name|surname|nachname|family[\s_-]?name)\b/],
    ["fullName", /\b(full[\s_-]?name|your[\s_-]?name|name\b.*\bcomplete|vollst)/],
    ["address", /\b(address|adresse|street|stra(ss|ß)e|anschrift)\b/],
    ["city", /\b(city|town|stadt|ort|wohnort)\b/],
    ["zip", /\b(zip|postal|postcode|plz|postleitzahl)\b/],
    ["country", /\b(country|land|staat)\b/],
    ["currentCompany", /\b(current\s+(employer|company)|aktuelle[r]?\s+arbeitgeber)\b/],
    ["salaryExpectation", /\b(salary|gehalt|compensation|gehaltsvorstellung|desired\s+pay)\b/],
    ["noticePeriod", /\b(notice\s+period|k(ü|u)ndigungsfrist|availability|verf(ü|u)gbar)\b/],
    // „name" generic DOAR la final, ca fallback — după first/last/full.
    ["fullName", /\bname\b/],
  ];

  function norm(s) {
    return String(s || "").toLowerCase().trim();
  }

  /**
   * ctx: {autocomplete, name, id, placeholder, label, type}
   * Întoarce cheia de profil sau null.
   */
  function matchField(ctx) {
    ctx = ctx || {};
    const type = norm(ctx.type);
    // Nu atingem parole, fișiere, butoane, hidden — niciodată autofill acolo.
    if (["password", "file", "hidden", "submit", "button", "checkbox", "radio"].includes(type)) {
      return null;
    }

    const ac = norm(ctx.autocomplete);
    if (ac && AUTOCOMPLETE[ac]) return AUTOCOMPLETE[ac];

    // Text combinat pentru euristici; label-ul e cel mai descriptiv, deci primul.
    // Spargem camelCase („phoneNumber"→„phone Number") ÎNAINTE de lowercase, apoi
    // separatorii (_ - [ ]) devin spații — altfel nici „candidate_email" nici
    // „phoneNumber" n-ar avea boundary înainte de cuvântul-cheie.
    const haystack = [ctx.label, ctx.name, ctx.id, ctx.placeholder]
      .map((x) => String(x || "").replace(/([a-z0-9])([A-Z])/g, "$1 $2"))
      .join(" ")
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, " ");
    if (!haystack.trim()) return null;

    for (const [key, re] of HEURISTICS) {
      if (re.test(haystack)) return key;
    }
    return null;
  }

  const api = { matchField, AUTOCOMPLETE, HEURISTICS };
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api; // Node (teste)
  } else {
    root.ATSMatcher = api; // browser (content script)
  }
})(typeof self !== "undefined" ? self : this);
