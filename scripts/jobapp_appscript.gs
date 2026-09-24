/**
 * Job Application Tracker — Apps Script
 *
 * Funcționează atât bound (Extensions → Apps Script din Sheet) cât și standalone.
 *
 * Setup:
 *   1. Lipește acest fișier ca Code.gs (înlocuiește tot)
 *   2. File → Project Settings → Script Properties:
 *        WEBHOOK_SECRET = <secretul HMAC, același din serverul webhook>
 *      (WEBHOOK_URL NU mai e necesar — e hardcodat mai jos la job.n8n-studio.com)
 *   3. În dropdown selectează `setupTriggers` → Run (autorizează scope-urile)
 *   4. (Opțional) Run `testWebhook` ca să verifici conexiunea (trebuie Status 200)
 *   5. (Opțional) Web trigger: adaugă un fișier HTML numit `WebTrigger` cu
 *      conținutul din `scripts/jobapp_webtrigger.html`, apoi
 *      Deploy → New deployment → Web app (Execute as: Me, Access: Only myself)
 */

// URL-ul serverului FastAPI (fostul jobs.get-hub.io, mort). Hardcodat ca să NU
// mai depindă de Script Properties — unde rămăsese URL-ul vechi.
const WEBHOOK_URL = "https://job.n8n-studio.com/trigger";

const SHEET_ID = "1fTjvs6GEYTzfwuOVsoJh-KAWW3EAB_bxV3m7UM1WM6M";
const STATUS_COL = 13; // coloana M (1-indexed) — Bewerbung send
const DANKE_STATUS_COL = 28; // coloana AB — Danke nach Absage
const TYP_COL = 5; // coloana E — Typ (paste URL here to auto-scrape)
const SEND_TRIGGER_VALUE = "send";
const HEADER_ROW = 1;
const NOTIZEN_COL = 23; // coloana W

// ============================================================
//  TRIGGER HANDLER
// ============================================================

/**
 * Installable onEdit trigger handler.
 * Apelat AUTOMAT de Google când utilizatorul editează o celulă.
 * NU rula manual din editor — `e` ar fi undefined.
 */
function onEditInstallable(e) {
  // Guard: dacă cineva apasă Run pe această funcție, nu crăpa
  if (!e || !e.range) {
    Logger.log(
      "⚠️ onEditInstallable a fost apelat fără event. Asta înseamnă că ai apăsat Run manual — această funcție se apelează DOAR de trigger. Folosește testWebhook în loc.",
    );
    return;
  }

  const range = e.range;

  // Guard: ignoră edits pe orice sheet în afara "Bewerbungen" (sheet principal)
  const sheetName = range.getSheet().getName();
  if (sheetName !== "Bewerbungen") return;

  const col = range.getColumn();
  if (col !== STATUS_COL && col !== DANKE_STATUS_COL && col !== TYP_COL) return;
  if (range.getRow() <= HEADER_ROW) return;

  const newValue = String(e.value || "").trim();
  const sheet = range.getSheet();
  const row = range.getRow();

  // ── URL paste în col E (Typ) → auto-scrape & fill ──
  if (col === TYP_COL) {
    if (newValue.toLowerCase().startsWith("http")) {
      processScrapeUrl(sheet, row, newValue);
    }
    return;
  }

  if (newValue.toLowerCase() !== SEND_TRIGGER_VALUE) return;

  if (col === STATUS_COL) {
    processSendForRow(sheet, row);
  } else {
    processDankeForRow(sheet, row);
  }
}

/**
 * URL în col E (Typ) — trimite la webhook care face scrape + auto-fill.
 * Apelat din onEdit (paste manual) ȘI din web trigger (submitJobUrl).
 * Întoarce {ok, code, body}; la eșec pune URL-ul înapoi în col E și loghează
 * motivul în col W, ca rândul să poată fi reîncercat prin re-paste.
 */
function processScrapeUrl(sheet, row, url) {
  sheet.getRange(row, TYP_COL).setValue("scraping...");
  SpreadsheetApp.flush();

  const payload = {
    action: "scrape_and_fill",
    sheet_id: SHEET_ID,
    row: row,
    url: url,
    timestamp: new Date().toISOString(),
  };

  try {
    const response = postToWebhook(payload);
    const code = response.getResponseCode();
    const body = response.getContentText();
    Logger.log("Scrape URL " + code + ": " + body);

    if (code < 200 || code >= 300) {
      sheet.getRange(row, TYP_COL).setValue(url);
      logError(sheet, row, "Scrape URL HTTP " + code + ": " + body);
      return { ok: false, code: code, body: body };
    }
    return { ok: true, code: code, body: body };
  } catch (err) {
    sheet.getRange(row, TYP_COL).setValue(url);
    logError(sheet, row, "Scrape URL error: " + err.message);
    return { ok: false, code: 0, body: err.message };
  }
}

// ============================================================
//  WEB TRIGGER — pagină unde lipești un link de job
// ============================================================
//
// De ce web app Apps Script și nu un endpoint în repo: trigger-ul onEdit NU
// se declanșează la scrieri prin API (n8n, gspread) — doar la editări făcute
// de om în UI. Un link scris în col E din afară ar rămâne acolo fără scrape.
// Web app-ul rulează în contextul Sheet-ului: adaugă rândul și apelează direct
// processScrapeUrl(), exact ca la paste-ul manual.
//
// Deploy: Deploy → New deployment → Web app
//   Execute as:     Me
//   Who has access: Only myself   (secretul HMAC rămâne în Script Properties)

const WEB_TRIGGER_TAB = "Bewerbungen";
const APPLY_URL_HEADER = "Apply URL";
const MAX_URL_LENGTH = 2000;

function doGet(e) {
  const tpl = HtmlService.createTemplateFromFile("WebTrigger");
  // Doar pre-completare (bookmarklet / share) — un GET nu declanșează nimic.
  tpl.prefillUrl = (e && e.parameter && e.parameter.url) || "";
  return tpl
    .evaluate()
    .setTitle("Job → Sheet")
    .addMetaTag("viewport", "width=device-width, initial-scale=1");
}

/**
 * Apelat din pagină (google.script.run). Adaugă un rând nou cu URL-ul în
 * col E și pornește scrape-ul. Refuză URL-urile deja prezente în Sheet.
 */
function submitJobUrl(rawUrl) {
  assertOwner_();
  const url = String(rawUrl || "").trim();
  if (!/^https?:\/\/\S+$/i.test(url) || url.length > MAX_URL_LENGTH) {
    return { ok: false, message: "Link invalid — trebuie să înceapă cu http(s)://" };
  }

  const ss = SpreadsheetApp.openById(SHEET_ID);
  const sheet = ss.getSheetByName(WEB_TRIGGER_TAB);
  if (!sheet) return { ok: false, message: "Tab-ul " + WEB_TRIGGER_TAB + " lipsește" };

  // Lock: două trimiteri simultane ar calcula același getLastRow() și ar
  // scrie în același rând.
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(20000)) {
    return { ok: false, message: "Sheet ocupat, reîncearcă în câteva secunde" };
  }
  let row;
  try {
    const existing = findRowByUrl_(sheet, url);
    if (existing) {
      return {
        ok: false,
        duplicate: true,
        row: existing,
        rowUrl: rowUrl_(ss, sheet, existing),
        message: "Linkul există deja în rândul " + existing,
      };
    }
    row = sheet.getLastRow() + 1;
    sheet.getRange(row, TYP_COL).setValue(url);
    SpreadsheetApp.flush();
  } finally {
    lock.releaseLock();
  }

  const res = processScrapeUrl(sheet, row, url);
  return {
    ok: res.ok,
    row: row,
    rowUrl: rowUrl_(ss, sheet, row),
    message: res.ok
      ? "Rândul " + row + " adăugat — scraping pornit"
      : "Rândul " + row + " adăugat, dar webhook-ul a eșuat (HTTP " + res.code + "): " + res.body,
  };
}

/** Starea rândului, pentru polling din pagină după trimitere. */
function getRowStatus(row) {
  assertOwner_();
  const sheet = SpreadsheetApp.openById(SHEET_ID).getSheetByName(WEB_TRIGGER_TAB);
  const r = Number(row);
  if (!sheet || !Number.isInteger(r) || r <= HEADER_ROW || r > sheet.getLastRow()) {
    return null;
  }
  const lastCol = sheet.getLastColumn();
  const headers = sheet.getRange(HEADER_ROW, 1, 1, lastCol).getValues()[0];
  const values = sheet.getRange(r, 1, 1, lastCol).getValues()[0];
  const pick = (name) => {
    const i = headers.indexOf(name);
    return i >= 0 ? String(values[i] || "") : "";
  };
  return {
    typ: String(values[TYP_COL - 1] || ""),
    firma: pick("Firma"),
    position: pick("Position"),
    standort: pick("Standort"),
    notiz: String(values[NOTIZEN_COL - 1] || "").split("\n")[0],
  };
}

/**
 * Caută URL-ul în col E și în „Apply URL" (acolo îl mută scrape-ul după
 * completare). Comparația ignoră slash-ul final și majusculele host-ului.
 */
function findRowByUrl_(sheet, url) {
  const lastRow = sheet.getLastRow();
  if (lastRow <= HEADER_ROW) return 0;
  const headers = sheet.getRange(HEADER_ROW, 1, 1, sheet.getLastColumn()).getValues()[0];
  const cols = [TYP_COL];
  const applyIdx = headers.indexOf(APPLY_URL_HEADER);
  if (applyIdx >= 0 && applyIdx + 1 !== TYP_COL) cols.push(applyIdx + 1);

  const target = normalizeUrl_(url);
  const n = lastRow - HEADER_ROW;
  for (const col of cols) {
    const vals = sheet.getRange(HEADER_ROW + 1, col, n, 1).getValues();
    for (let i = 0; i < n; i++) {
      if (normalizeUrl_(vals[i][0]) === target) return HEADER_ROW + 1 + i;
    }
  }
  return 0;
}

function normalizeUrl_(value) {
  const s = String(value || "").trim().replace(/\/+$/, "");
  return s.replace(/^(https?:\/\/[^\/?#]+)/i, (m) => m.toLowerCase());
}

function rowUrl_(ss, sheet, row) {
  return ss.getUrl() + "#gid=" + sheet.getSheetId() + "&range=E" + row;
}

/**
 * Apărare în adâncime: dacă web app-ul ajunge din greșeală deployat cu
 * „Anyone", un vizitator străin ar putea scrie în Sheet cu drepturile
 * proprietarului. Pe „Only myself" condiția e mereu adevărată.
 */
function assertOwner_() {
  const active = Session.getActiveUser().getEmail();
  const owner = Session.getEffectiveUser().getEmail();
  if (!active || active !== owner) {
    throw new Error("Acces refuzat");
  }
}

/**
 * Trimite mesaj de mulțumire pentru o respingere (acțiune send_thankyou_rejection).
 */
function processDankeForRow(sheet, row) {
  sheet.getRange(row, DANKE_STATUS_COL).setValue("running");
  SpreadsheetApp.flush();

  const lastCol = sheet.getLastColumn();
  const headers = sheet.getRange(HEADER_ROW, 1, 1, lastCol).getValues()[0];
  const values = sheet.getRange(row, 1, 1, lastCol).getValues()[0];
  const rowData = {};
  headers.forEach((h, i) => {
    rowData[h] = values[i];
  });

  const payload = {
    action: "send_thankyou_rejection",
    sheet_id: SHEET_ID,
    row: row,
    data: rowData,
    timestamp: new Date().toISOString(),
  };

  try {
    const response = postToWebhook(payload);
    const code = response.getResponseCode();
    const body = response.getContentText();
    Logger.log("Webhook (danke) " + code + ": " + body);

    if (code < 200 || code >= 300) {
      sheet.getRange(row, DANKE_STATUS_COL).setValue("error");
      logError(sheet, row, "Danke HTTP " + code + ": " + body);
    }
  } catch (err) {
    sheet.getRange(row, DANKE_STATUS_COL).setValue("error");
    logError(sheet, row, "Danke exception: " + err.message);
  }
}

/**
 * Logica efectivă: marchează running, trimite la webhook, gestionează răspunsul.
 */
function processSendForRow(sheet, row) {
  sheet.getRange(row, STATUS_COL).setValue("running");
  SpreadsheetApp.flush();

  const lastCol = sheet.getLastColumn();
  const headers = sheet.getRange(HEADER_ROW, 1, 1, lastCol).getValues()[0];
  const values = sheet.getRange(row, 1, 1, lastCol).getValues()[0];
  const rowData = {};
  headers.forEach((h, i) => {
    rowData[h] = values[i];
  });

  const payload = {
    action: "send_application",
    sheet_id: SHEET_ID,
    row: row,
    data: rowData,
    timestamp: new Date().toISOString(),
  };

  try {
    const response = postToWebhook(payload);
    const code = response.getResponseCode();
    const body = response.getContentText();
    Logger.log("Webhook " + code + ": " + body);

    if (code < 200 || code >= 300) {
      sheet.getRange(row, STATUS_COL).setValue("error");
      logError(sheet, row, "HTTP " + code + ": " + body);
    }
    // Pentru 2xx — serverul setează status în "done"/"error" via Sheets API
  } catch (err) {
    sheet.getRange(row, STATUS_COL).setValue("error");
    logError(sheet, row, "Exception: " + err.message);
  }
}

// ============================================================
//  HTTP / HMAC
// ============================================================

function postToWebhook(payload) {
  const props = PropertiesService.getScriptProperties();
  const secret = props.getProperty("WEBHOOK_SECRET");

  if (!secret) {
    throw new Error("Lipsește Script Property: WEBHOOK_SECRET");
  }

  const body = JSON.stringify(payload);
  const signature = computeHmac(body, secret);

  return UrlFetchApp.fetch(WEBHOOK_URL, {
    method: "post",
    contentType: "application/json",
    payload: body,
    headers: { "X-Signature": signature },
    muteHttpExceptions: true,
  });
}

function computeHmac(body, secret) {
  const bodyBytes = Utilities.newBlob(body).getBytes();
  const keyBytes = Utilities.newBlob(secret).getBytes();
  const raw = Utilities.computeHmacSha256Signature(bodyBytes, keyBytes);
  return raw
    .map((b) => {
      const v = (b < 0 ? b + 256 : b).toString(16);
      return v.length === 1 ? "0" + v : v;
    })
    .join("");
}

function logError(sheet, row, msg) {
  const existing = sheet.getRange(row, NOTIZEN_COL).getValue() || "";
  const stamped = "[" + new Date().toISOString() + "] " + msg;
  sheet
    .getRange(row, NOTIZEN_COL)
    .setValue(stamped + (existing ? "\n" + existing : ""));
}

// ============================================================
//  SETUP — rulează O SINGURĂ DATĂ după ce setezi WEBHOOK_SECRET
// ============================================================

function setupTriggers() {
  const ss = SpreadsheetApp.openById(SHEET_ID);

  const triggers = ScriptApp.getProjectTriggers();
  let removed = 0;
  triggers.forEach((t) => {
    if (t.getHandlerFunction() === "onEditInstallable") {
      ScriptApp.deleteTrigger(t);
      removed++;
    }
  });
  Logger.log("Removed " + removed + " old triggers");

  ScriptApp.newTrigger("onEditInstallable")
    .forSpreadsheet(ss)
    .onEdit()
    .create();
  Logger.log("✓ Created installable onEdit trigger for sheet " + SHEET_ID);

  const props = PropertiesService.getScriptProperties();
  const secret = props.getProperty("WEBHOOK_SECRET");

  let summary;
  if (!secret) {
    summary =
      "⚠️ Trigger creat, dar LIPSEȘTE WEBHOOK_SECRET.\n" +
      "Mergi la: ⚙️ Project Settings → Script Properties → Add\n" +
      "  WEBHOOK_SECRET = <secretul HMAC din server>\n" +
      "WEBHOOK_URL e hardcodat: " + WEBHOOK_URL;
  } else {
    summary = "✓ Setup complet.\nTrigger instalat.\nWebhook URL: " + WEBHOOK_URL;
  }
  Logger.log(summary);

  try {
    SpreadsheetApp.getUi().alert(summary);
  } catch (e) {
    Logger.log("(UI alert skipped: standalone script)");
  }
}

// ============================================================
//  TEST UTILITIES
// ============================================================

function testWebhook() {
  const response = postToWebhook({
    action: "ping",
    timestamp: new Date().toISOString(),
  });
  const code = response.getResponseCode();
  const body = response.getContentText();
  Logger.log("URL: " + WEBHOOK_URL);
  Logger.log("Status: " + code);
  Logger.log("Body: " + body);

  try {
    SpreadsheetApp.getUi().alert("Ping → " + code + "\n" + body);
  } catch (e) {
    /* standalone */
  }
}

function testProcessRow() {
  const ROW_TO_TEST = 2;
  const ss = SpreadsheetApp.openById(SHEET_ID);
  const sheet = ss.getSheetByName("Bewerbungen") || ss.getSheets()[0];
  processSendForRow(sheet, ROW_TO_TEST);
}

// ============================================================
//  MENIU CUSTOM (doar în context bound)
// ============================================================

function onOpen() {
  try {
    SpreadsheetApp.getUi()
      .createMenu("🚀 Job Tracker")
      .addItem("Test Webhook", "testWebhook")
      .addItem("Trigger selected row (send Bewerbung)", "triggerSelectedRow")
      .addItem("Trigger selected row (Danke nach Absage)", "triggerDankeSelectedRow")
      .addToUi();
  } catch (e) {
    /* standalone */
  }
}

function triggerSelectedRow() {
  const sheet = SpreadsheetApp.getActiveSheet();
  const row = sheet.getActiveCell().getRow();
  if (row <= HEADER_ROW) {
    SpreadsheetApp.getUi().alert("Selectează un rând de date (nu header-ul).");
    return;
  }
  processSendForRow(sheet, row);
}

function triggerDankeSelectedRow() {
  const sheet = SpreadsheetApp.getActiveSheet();
  const row = sheet.getActiveCell().getRow();
  if (row <= HEADER_ROW) {
    SpreadsheetApp.getUi().alert("Selectează un rând de date (nu header-ul).");
    return;
  }
  processDankeForRow(sheet, row);
}
