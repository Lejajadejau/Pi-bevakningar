/**
 * Koppling mellan Pi-bevakningen "HD – nytt avgörande" och Google-dokumentet
 * "Claude HD-bevakning". Pi:n skickar varje nytt avgörande hit, och skriptet
 * lägger in det överst i dokumentet (nyast först).
 *
 * Installation: se README, avsnittet "HD-bevakningen och Google-dokumentet".
 * Byt ut NYCKEL mot den hemliga nyckeln innan du sparar.
 */

const DOKUMENT_ID = '1BfICoZw3f6HjhouSF0TIlvYgds7u2V0wIp_z_RRclHs';
const NYCKEL = 'BYT-UT-MOT-NYCKELN';
const RUBRIK = 'Claude HD-bevakning';

function doPost(e) {
  let data;
  try {
    data = JSON.parse(e.postData.contents);
  } catch (fel) {
    return svar('fel: ogiltig data');
  }
  if (data.nyckel !== NYCKEL) return svar('fel: fel nyckel');

  const lock = LockService.getScriptLock();
  lock.waitLock(30000);
  try {
    skrivAvgorande(data);
  } finally {
    lock.releaseLock();
  }
  return svar('ok');
}

function svar(text) {
  return ContentService.createTextOutput(text).setMimeType(ContentService.MimeType.TEXT);
}

function skrivAvgorande(a) {
  const body = DocumentApp.openById(DOKUMENT_ID).getBody();
  const forsta = body.getNumChildren() > 0 ? body.getChild(0) : null;
  const harRubrik = forsta && forsta.getType() === DocumentApp.ElementType.PARAGRAPH &&
    forsta.asParagraph().getText() === RUBRIK;
  if (!harRubrik) {
    body.insertParagraph(0, RUBRIK).setHeading(DocumentApp.ParagraphHeading.TITLE);
    body.insertParagraph(1, 'Nya avgöranden från Högsta domstolen (inte prövningstillstånd), nyast först. ' +
      'Sammanfattningarna är HD:s egna. Uppdateras automatiskt av Pi-bevakningen.')
      .setHeading(DocumentApp.ParagraphHeading.NORMAL).setItalic(true);
  }

  // Bygg posten och lägg in den direkt efter rubrik och ingress.
  const delar = [];
  const titel = a.malnummer + (a.benamning ? ' – ”' + a.benamning + '”' : '');
  delar.push([titel, DocumentApp.ParagraphHeading.HEADING2]);
  delar.push(['Avgjort ' + a.avgorandedatum + ' · publicerat ' + a.publicerat, null]);
  delar.push([a.sammanfattning || '(HD har inte lagt in någon sammanfattning.)', null]);
  if (a.nyckelord && a.nyckelord.length) delar.push(['Nyckelord: ' + a.nyckelord.join(', '), null]);
  if (a.lagrum && a.lagrum.length) delar.push(['Lagrum: ' + a.lagrum.join('; '), null]);
  delar.push(['Öppna i Sök rättspraxis', 'lank']);

  let index = 2;
  delar.forEach(function (del) {
    const p = body.insertParagraph(index++, del[0]);
    p.setItalic(false).setBold(false);
    if (del[1] === 'lank') {
      p.setHeading(DocumentApp.ParagraphHeading.NORMAL).setLinkUrl(a.lank);
    } else {
      p.setHeading(del[1] || DocumentApp.ParagraphHeading.NORMAL);
    }
  });
  body.insertHorizontalRule(index);
}

/** Kör den här i Apps Script-redigeraren för att prova att skrivningen fungerar. */
function provskriv() {
  skrivAvgorande({
    malnummer: 'TEST 1-26', benamning: 'Provpost', avgorandedatum: 'idag', publicerat: '—',
    sammanfattning: 'Detta är en provpost från Apps Script. Ta gärna bort den.',
    nyckelord: ['Test'], lagrum: [], lank: 'https://rattspraxis.etjanst.domstol.se/sok/sokning?domstolskod=HDO'
  });
}
