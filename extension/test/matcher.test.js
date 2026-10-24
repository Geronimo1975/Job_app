/*
 * Test pentru matcher.js — rulabil cu `node --test extension/test/matcher.test.js`.
 * Fără framework extern: folosește node:test + node:assert.
 */
const { test } = require("node:test");
const assert = require("node:assert");
const { matchField, pickOption } = require("../matcher.js");

test("autocomplete has top priority", () => {
  assert.equal(matchField({ autocomplete: "given-name", label: "irrelevant" }), "firstName");
  assert.equal(matchField({ autocomplete: "family-name" }), "lastName");
  assert.equal(matchField({ autocomplete: "email" }), "email");
  assert.equal(matchField({ autocomplete: "tel" }), "phone");
});

test("label heuristics (EN)", () => {
  assert.equal(matchField({ label: "First Name" }), "firstName");
  assert.equal(matchField({ label: "Last Name" }), "lastName");
  assert.equal(matchField({ label: "Email Address" }), "email");
  assert.equal(matchField({ label: "Phone" }), "phone");
  assert.equal(matchField({ label: "LinkedIn Profile" }), "linkedin");
});

test("label heuristics (DE)", () => {
  assert.equal(matchField({ label: "Vorname" }), "firstName");
  assert.equal(matchField({ label: "Nachname" }), "lastName");
  assert.equal(matchField({ label: "Telefonnummer" }), "phone");
  assert.equal(matchField({ label: "Wohnort" }), "city");
  assert.equal(matchField({ label: "Gehaltsvorstellung" }), "salaryExpectation");
});

test("first/last win over generic name", () => {
  // „First name" nu trebuie să cadă pe fullName doar pentru că conține „name".
  assert.equal(matchField({ name: "first_name" }), "firstName");
  assert.equal(matchField({ label: "Your Name" }), "fullName");
  assert.equal(matchField({ name: "name" }), "fullName");
});

test("matches on name/id/placeholder too", () => {
  assert.equal(matchField({ name: "candidate_email" }), "email");
  assert.equal(matchField({ id: "phoneNumber" }), "phone");
  assert.equal(matchField({ placeholder: "https://linkedin.com/in/…" }), "linkedin");
});

test("never touches sensitive/non-text fields", () => {
  assert.equal(matchField({ type: "password", label: "Email" }), null);
  assert.equal(matchField({ type: "file", label: "Resume" }), null);
  assert.equal(matchField({ type: "hidden", name: "email" }), null);
  assert.equal(matchField({ type: "checkbox", label: "First Name" }), null);
});

test("unknown field returns null", () => {
  assert.equal(matchField({ label: "Favourite colour" }), null);
  assert.equal(matchField({}), null);
  assert.equal(matchField({ label: "" }), null);
});

test("greenhouse-style attributes", () => {
  assert.equal(matchField({ id: "first_name", name: "job_application[first_name]" }), "firstName");
  assert.equal(matchField({ id: "last_name", name: "job_application[last_name]" }), "lastName");
});

test("matchField accepts select type (nu e în lista respinsă)", () => {
  assert.equal(matchField({ type: "select", label: "Country" }), "country");
});

test("pickOption: egalitate pe text și pe value", () => {
  const opts = [
    { value: "", text: "Please select…" },
    { value: "DE", text: "Germany" },
    { value: "AT", text: "Austria" },
  ];
  assert.equal(pickOption(opts, "Germany"), 1); // text
  assert.equal(pickOption(opts, "germany"), 1); // case-insensitive
  assert.equal(pickOption(opts, "DE"), 1); // value
  assert.equal(pickOption(opts, "Austria"), 2);
});

test("pickOption: diacritice și punctuație normalizate", () => {
  const opts = [
    { value: "a", text: "Zürich" },
    { value: "b", text: "Düsseldorf" },
  ];
  assert.equal(pickOption(opts, "Zurich"), 0);
  assert.equal(pickOption(opts, "dusseldorf"), 1);
});

test("pickOption: conținere pe cuvinte întregi, în ambele sensuri", () => {
  const opts = [
    { value: "", text: "—" },
    { value: "de", text: "Deutschland (Germany)" },
  ];
  // opțiunea conține toate cuvintele căutate.
  assert.equal(pickOption(opts, "Deutschland"), 1);
  // căutarea conține toate cuvintele opțiunii.
  assert.equal(pickOption([{ value: "x", text: "Berlin" }], "Berlin, Germany"), 0);
});

test("pickOption: conservator — fără sinonime, fără potrivire greșită", () => {
  const opts = [
    { value: "", text: "Please select…" },
    { value: "DE", text: "Deutschland" },
  ];
  // „Germany" NU trebuie să nimerească „Deutschland" (n-avem tabel de sinonime).
  assert.equal(pickOption(opts, "Germany"), null);
  // placeholder gol nu se potrivește cu valoare goală.
  assert.equal(pickOption(opts, ""), null);
  assert.equal(pickOption([], "Germany"), null);
});
