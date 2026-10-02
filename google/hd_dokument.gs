/**
 * Koppling mellan Pi-bevakningen "HD – nytt avgörande" och Google-dokumentet
 * "Claude HD-bevakning". Pi:n skickar varje avgörande hit som en lista block,
 * och skriptet lägger in det överst i dokumentet (nyast först). Finns
 * avgörandet redan i dokumentet ersätts det på samma plats.
 *
 * Installation: se README, avsnittet "HD-bevakningen och Google-dokumentet".
 */

const DOKUMENT_ID = '1BfICoZw3f6HjhouSF0TIlvYgds7u2V0wIp_z_RRclHs';
const NYCKEL = 'ivD7N72qrUVczgToNvb9oTkWNKuSa_Lf';
const RUBRIK = 'Claude HD-bevakning';
const INGRESS = 'Nya avgöranden från Högsta domstolen (inte prövningstillstånd), nyast först. ' +
  'Sammanfattningarna skrivs automatiskt utifrån hela avgörandet och kan innehålla fel – ' +
  'kontrollera mot avgörandet innan du citerar.';

function doPost(e) {
  let data;
  try {
    data = JSON.parse(e.postData.contents);
  } catch (fel) {
    return svar('fel: ogiltig data');
  }
  if (data.nyckel !== NYCKEL) return svar('fel: fel nyckel');
  if (!data.block) return svar('fel: gammalt format – uppdatera Pi:n (git pull)');

  const lock = LockService.getScriptLock();
  lock.waitLock(30000);
  try {
    skrivAvgorande(data.malnummer, data.block);
  } finally {
    lock.releaseLock();
  }
  return svar('ok');
}

function svar(text) {
  return ContentService.createTextOutput(text).setMimeType(ContentService.MimeType.TEXT);
}

function arHorisontellLinje(el) {
  return el.getType() === DocumentApp.ElementType.PARAGRAPH &&
    el.asParagraph().findElement(DocumentApp.ElementType.HORIZONTAL_RULE) !== null;
}

/** Ser till att rubrik och ingress står överst. Returnerar index där poster börjar. */
function sakerstallHuvud(body) {
  const forsta = body.getNumChildren() > 0 ? body.getChild(0) : null;
  const harRubrik = forsta && forsta.getType() === DocumentApp.ElementType.PARAGRAPH &&
    forsta.asParagraph().getText() === RUBRIK;
  if (!harRubrik) {
    body.insertParagraph(0, RUBRIK).setHeading(DocumentApp.ParagraphHeading.TITLE);
    body.insertParagraph(1, INGRESS).setHeading(DocumentApp.ParagraphHeading.NORMAL).setItalic(true);
  } else {
    body.getChild(1).asParagraph().setText(INGRESS);
    body.getChild(1).asParagraph().setItalic(true);
  }
  return 2;
}

/** Tar bort en befintlig post för samma målnummer. Returnerar dess index, eller -1. */
function taBortBefintlig(body, malnummer) {
  for (let i = 2; i < body.getNumChildren(); i++) {
    const el = body.getChild(i);
    if (el.getType() !== DocumentApp.ElementType.PARAGRAPH) continue;
    const p = el.asParagraph();
    if (p.getHeading() !== DocumentApp.ParagraphHeading.HEADING2) continue;
    const t = p.getText();
    if (t === malnummer || t.indexOf(malnummer + ' –') === 0) {
      // Ta bort från rubriken till och med nästa horisontella linje.
      while (i < body.getNumChildren() - 1) {
        const slut = arHorisontellLinje(body.getChild(i));
        body.removeChild(body.getChild(i));
        if (slut) break;
      }
      return i;
    }
  }
  return -1;
}

function nyttStycke(body, index, text) {
  const p = body.insertParagraph(index, text);
  p.setHeading(DocumentApp.ParagraphHeading.NORMAL);
  p.editAsText().setBold(false).setItalic(false).setFontSize(11).setLinkUrl(null);
  return p;
}

function skrivAvgorande(malnummer, block) {
  const body = DocumentApp.openById(DOKUMENT_ID).getBody();
  const start = sakerstallHuvud(body);
  const befintlig = taBortBefintlig(body, malnummer);
  let index = befintlig >= 0 ? befintlig : start;

  block.forEach(function (b) {
    if (b.typ === 'rubrik') {
      body.insertParagraph(index++, b.text).setHeading(DocumentApp.ParagraphHeading.HEADING2);
    } else if (b.typ === 'meta') {
      const p = nyttStycke(body, index++, b.text);
      p.editAsText().setItalic(true).setFontSize(9).setForegroundColor('#666666');
    } else if (b.typ === 'avsnitt') {
      const stycken = String(b.text).split(/\n\s*\n|\n/).filter(function (s) { return s.trim(); });
      stycken.forEach(function (s, n) {
        const text = n === 0 ? b.etikett + '. ' + s.trim() : s.trim();
        const p = nyttStycke(body, index++, text);
        if (n === 0) p.editAsText().setBold(0, b.etikett.length, true);
      });
    } else if (b.typ === 'lank') {
      nyttStycke(body, index++, b.text).editAsText().setLinkUrl(b.url);
    } else {
      nyttStycke(body, index++, b.text);
    }
  });
  body.insertHorizontalRule(index);
}

/** Kör den här i Apps Script-redigeraren för att prova att skrivningen fungerar. */
function provskriv() {
  skrivAvgorande('TEST 1-26', [
    { typ: 'rubrik', text: 'TEST 1-26 – ”Provpost”' },
    { typ: 'meta', text: 'Avgjort idag · Test' },
    { typ: 'avsnitt', etikett: 'I korthet', text: 'Detta är en provpost från Apps Script. Ta gärna bort den.' },
    { typ: 'lank', text: 'Sök rättspraxis', url: 'https://rattspraxis.etjanst.domstol.se/sok/sokning?domstolskod=HDO' }
  ]);
}
