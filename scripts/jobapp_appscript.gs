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
 * URL paste în col E (Typ) — trimite la webhook care face scrape + auto-fill.
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
    }
  } catch (err) {
    sheet.getRange(row, TYP_COL).setValue(url);
    logError(sheet, row, "Scrape URL error: " + err.message);
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
