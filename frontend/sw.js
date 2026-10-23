/* Rov.E Service Worker — Fundament für Push-Benachrichtigungen (27.07.2026)
 *
 * ⚠️ WICHTIGSTE REGEL: Dieser Worker hat BEWUSST KEINEN fetch-Handler.
 * Ein Service Worker, der Anfragen abfängt und aus einem Cache beantwortet, ist der häufigste
 * Grund dafür, dass eine installierte PWA für immer eine alte Version zeigt. Genau das würde die
 * Auto-Update-Fähigkeit der App zerstören — und die ist der Grund, warum niemand Rov.E löschen und
 * neu installieren muss (was den Login killen würde).
 *
 * Ohne fetch-Handler kann dieser Worker die Auslieferung NICHT beeinflussen. Jede Anfrage geht
 * unverändert ans Netz. Wer hier Caching einbaut, muss vorher wissen, wie die App danach
 * aktualisiert wird — sonst nicht anfassen.
 *
 * Was er kann: eingehende Push-Nachrichten anzeigen und beim Antippen die App öffnen.
 */

// Sofort übernehmen statt auf das Schliessen aller Tabs zu warten — sonst läuft nach einem Update
// tagelang die alte Worker-Version weiter.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

function validNotificationTarget(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const type = String(value.type || "");
  const keys = Object.keys(value).sort().join(",");
  if (type === "report" && keys === "month,type" && /^20\d{2}-(0[1-9]|1[0-2])$/.test(String(value.month || ""))) {
    return { type: "report", month: String(value.month) };
  }
  if (["monthlyPlan", "analysis", "transactions"].includes(type) && keys === "type") {
    return { type };
  }
  return null;
}

function appUrlForTarget(target) {
  const url = new URL("./", self.registration.scope);
  if (target) {
    url.searchParams.set("rove_target", target.type);
    if (target.month) url.searchParams.set("month", target.month);
  }
  return url.toString();
}

function validLegacyAppUrl(value) {
  return value === "./#add" ? value : "./";
}

self.addEventListener("push", (event) => {
  let daten = {};
  try { daten = event.data ? event.data.json() : {}; } catch (e) { daten = {}; }

  const titel = daten.title || "Rov.E";
  const optionen = {
    body: daten.body || "",
    icon: "app-icon.png",
    badge: "app-icon.png",
    tag: daten.tag || "rove",          // gleiche tag = ersetzt statt stapelt, kein Spam
    renotify: false,
    // Ziele sind strukturiert und allowlisted. Alte url-Payloads bleiben als sicherer
    // App-Einstieg kompatibel, duerfen aber nie eine fremde URL oeffnen.
    data: {
      target: validNotificationTarget(daten.target),
      legacyUrl: validLegacyAppUrl(daten.url),
    },
  };
  event.waitUntil(self.registration.showNotification(titel, optionen));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const target = validNotificationTarget(event.notification.data && event.notification.data.target);
  const legacyUrl = validLegacyAppUrl(event.notification.data && event.notification.data.legacyUrl);
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((fenster) => {
      // Laeuft die App schon? Fokus plus Nachricht erhaelt deren bestehenden Zustand.
      for (const f of fenster) {
        if ("focus" in f) {
          return Promise.resolve(f.focus()).then(() => {
            f.postMessage({ type: "rove:notification-target", target });
          });
        }
      }
      return self.clients.openWindow(target ? appUrlForTarget(target) : new URL(legacyUrl, self.registration.scope).toString());
    })
  );
});
