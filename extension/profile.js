/*
 * profile.js — definiția câmpurilor de profil, partajată de options + popup.
 * Cheile corespund celor întoarse de matcher.js. Nicio dată aici — profilul
 * real stă în chrome.storage.local, completat de user în pagina de opțiuni.
 */
const PROFILE_FIELDS = [
  { key: "firstName", label: "Vorname" },
  { key: "lastName", label: "Nachname" },
  { key: "fullName", label: "Vollständiger Name" },
  { key: "email", label: "E-Mail" },
  { key: "phone", label: "Telefon" },
  { key: "address", label: "Adresse" },
  { key: "city", label: "Stadt" },
  { key: "zip", label: "PLZ" },
  { key: "country", label: "Land" },
  { key: "linkedin", label: "LinkedIn-URL" },
  { key: "github", label: "GitHub-URL" },
  { key: "website", label: "Website / Portfolio" },
  { key: "currentCompany", label: "Aktueller Arbeitgeber" },
  { key: "salaryExpectation", label: "Gehaltsvorstellung" },
  { key: "noticePeriod", label: "Kündigungsfrist / Verfügbarkeit" },
];

const STORAGE_KEY = "ats_profile";

if (typeof module !== "undefined" && module.exports) {
  module.exports = { PROFILE_FIELDS, STORAGE_KEY };
}
