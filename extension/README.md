# Bewerbung Autofill — Chrome-Extension

Completează formularele ATS de candidatură (Greenhouse, Lever, Ashby, Workday,
Personio și altele) din profilul tău local. **Completează doar — nu trimite
niciodată formularul.** Adresează segmentul `Bewerbungsart: Portal` din pipeline,
pe care altfel aplici manual.

## Principii (intenționat)
- **Nu auto-submit.** Umple câmpurile; tu revizuiești și apeși Submit.
- **Zero date în cod.** Profilul stă în `chrome.storage.local`, pe mașina ta; îl
  completezi în pagina de opțiuni. Nimic nu pleacă de pe dispozitiv.
- **Permisiuni minime.** `activeTab` + `scripting` + `storage` — fără
  host-permissions largi; scriptul se injectează doar când apeși butonul, pe
  tabul activ.
- **Nu atinge** parole, câmpuri de fișier (CV-ul îl încarci tu), hidden sau
  câmpuri deja completate.

## Instalare (dev / unpacked)
1. Chrome → `chrome://extensions`
2. Activează **Developer mode** (dreapta sus)
3. **Load unpacked** → selectează folderul `extension/`
4. Deschide **Profil bearbeiten** (din popup) și completează-ți datele → Speichern

## Folosire
1. Deschide o pagină de candidatură ATS
2. Click pe iconița extensiei → **Diese Seite ausfüllen**
3. Verifică câmpurile completate, corectează ce trebuie, trimite TU formularul

## Test
- Logica de potrivire câmp→profil: `node --test extension/test/matcher.test.js`
- Manual: deschide `extension/test/sample-form.html` în Chrome și rulează autofill;
  ar trebui completate numele, email, telefon, PLZ, Wohnort, Gehalt, LinkedIn —
  și NU parola / câmpul de fișier.

## Structură
| Fișier | Rol |
|---|---|
| `matcher.js` | potrivire câmp→cheie de profil (pur, testat în Node) |
| `content.js` | scanează DOM-ul, extrage label-ul, completează (injectat la click) |
| `popup.html/js` | butonul „Ausfüllen" + status |
| `options.html/js` | editarea profilului (chrome.storage.local) |
| `profile.js` | definiția câmpurilor, partajată |
| `test/` | test Node pentru matcher + formular demo |

## Dropdown-uri (`<select>`)
Completează ȘI `<select>`-urile native (Greenhouse/Lever/Personio): câmpul e
potrivit prin aceeași euristică, iar valoarea de profil e legată de o `<option>`
prin `pickOption` (pur, testat). Potrivirea opțiunii e conservatoare — egalitate
(exactă/normalizată pe diacritice) sau conținere de cuvinte întregi. NU există
tabel de sinonime de țări: profil „Germany" nu nimerește o opțiune „Deutschland",
pentru a nu alege greșit țara. Un select cu alegere reală deja făcută nu e atins.

## Limite cunoscute
- Comboboxele JS custom (Workday: `<button role="combobox">` + listbox, nu
  `<select>`) rămân manuale — a le deschide și naviga programatic e fragil și
  poate declanșa dialoguri. `<select>` nativ e acoperit (mai sus).
- Potrivirea e euristică (autocomplete + label/name/placeholder); câmpurile
  exotice rămân necompletate, niciodată completate greșit cu date sensibile.
