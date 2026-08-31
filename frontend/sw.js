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
    data: { url: daten.url || "./" },
  };
  event.waitUntil(self.registration.showNotification(titel, optionen));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const ziel = (event.notification.data && event.notification.data.url) || "./";
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((fenster) => {
      // Läuft die App schon irgendwo? Dann dorthin, statt einen zweiten Zustand aufzumachen.
      for (const f of fenster) {
        if ("focus" in f) return f.focus();
      }
      return self.clients.openWindow(ziel);
    })
  );
});
