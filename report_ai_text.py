"""
KI-gestuetzte, personalisierte Report-Texte fuer Rov.E.

Nimmt die fertig berechneten Report-Daten (alle Zahlen stehen bereits fest) und
laesst gpt-4o-mini daraus individuelle, auf das konkrete Verhalten des jeweiligen
Nutzers zugeschnittene Textbloecke formulieren. Die KI erfindet KEINE Zahlen -
sie bekommt die fertigen Werte und formuliert nur die Sprache drumherum.

Sicherheit / Fallback:
- Ohne OPENAI_API_KEY (z.B. lokal) wird sofort ein leeres Dict zurueckgegeben.
- Bei JEDEM Fehler (Rate-Limit, Timeout, ungueltiges JSON, ...) -> leeres Dict.
- Die Renderer nutzen bei fehlenden Keys ihre bestehenden Formel-Texte.
  Der Report funktioniert also immer, mit oder ohne KI.

Kill-Switch (ohne Code-Deploy): Umgebungsvariable ROVE_AI_REPORT_TEXT=0 setzen,
dann faellt der Report vollstaendig auf die Formel-Texte zurueck.
"""

import os
import json
import logging

logger = logging.getLogger(__name__)

AI_TEXT_MODEL = "gpt-4o-mini"
REQUEST_TIMEOUT = 12  # Sekunden - Report-Erzeugung darf nie am API-Call haengen
MAX_FIELD_LEN = 200   # laengere Antworten pro Feld werden verworfen (Layout-Schutz)

# Erlaubte Ausgabe-Felder. Nur diese Keys werden aus der KI-Antwort uebernommen.
ALLOWED_FIELDS = (
    "development",
    "best_decision",
    "focus",
    "goal_honest",
    "goal_lever",
    "recap_good",
    "recap_attention",
    "recap_lever",
)

_SCORE_PART_LABELS = {
    "consistency": "Tracking-Konstanz",
    "budget": "Budget-Kontrolle",
    "savings": "Sparrate-Umsetzung",
    "structure": "finanzielle Struktur",
}


def _enabled() -> bool:
    flag = os.getenv("ROVE_AI_REPORT_TEXT", "1").strip().lower()
    return flag not in ("0", "false", "off", "no", "")


def _eur(value) -> str:
    try:
        return f"{float(value):,.0f} EUR".replace(",", ".")
    except (TypeError, ValueError):
        return "0 EUR"


def _weakest_score_part(parts: dict) -> str:
    if not isinstance(parts, dict):
        return ""
    candidates = {k: parts.get(k) for k in _SCORE_PART_LABELS if isinstance(parts.get(k), (int, float))}
    if not candidates:
        return ""
    weakest = min(candidates, key=candidates.get)
    return _SCORE_PART_LABELS.get(weakest, "")


def _build_signals(data: dict) -> str:
    """Baut aus den Report-Daten einen kompakten, gelabelten Datenblock fuers Prompt."""
    meta = data.get("meta", {})
    profile = data.get("profile", {})
    pages = data.get("pages", {})
    month = pages.get("month", {})
    score = pages.get("score", {})
    goal = pages.get("goal", {})
    story = pages.get("financial_story", {})
    wealth = pages.get("wealth_journey", {})

    strongest = month.get("strongest_category") or {}
    biggest = month.get("biggest_expense") or {}
    inv_summary = wealth.get("investment_summary") or {}
    execution = wealth.get("monthly_execution") or {}

    remaining = month.get("remaining_budget")
    budget_line = "im Rahmen" if (remaining is None or remaining >= 0) else f"{_eur(abs(remaining))} ueberzogen"

    delta = story.get("delta")
    if delta is None:
        dev_line = "erster Referenzmonat, noch kein Vormonatsvergleich"
    elif delta >= 0:
        dev_line = f"Nettovermoegen +{_eur(delta)} ggü. Vormonat"
    else:
        dev_line = f"Nettovermoegen -{_eur(abs(delta))} ggü. Vormonat"

    months_to_goal = goal.get("months_to_goal")
    if months_to_goal:
        # Jahres-Formatierung nutzen (z.B. "52 Jahre und 5 Monate"), NICHT rohe Monate.
        try:
            from report_engine import format_month_duration
            dur = format_month_duration(months_to_goal)
        except Exception:
            dur = f"{int(months_to_goal)} Monate"
        goal_eta = f"noch etwa {dur} bei aktueller Sparrate"
    else:
        goal_eta = "noch nicht berechenbar (keine/zu kleine Sparrate)"

    weakest = _weakest_score_part(score.get("parts") or {})

    lines = [
        f"Monat: {meta.get('month_label', '-')}",
        f"Tracking-Tage diesen Monat: {meta.get('tracked_days', 0)}",
        f"Nettoeinkommen gesamt: {_eur(profile.get('income_total'))}",
        f"Fixkosten: {_eur(profile.get('fixed_costs'))}",
        f"Variable Ausgaben diesen Monat: {_eur(month.get('total_expenses'))}",
        f"Freies Budget-Ergebnis: {budget_line}",
        f"Geplante Sparrate/Monat: {_eur(profile.get('savings_plan'))} (Sparquote {round(profile.get('savings_rate') or 0)}%)",
        f"Investiert/zurueckgelegt diesen Monat: {_eur(inv_summary.get('net_contributions'))}",
        "Monatsplan bestaetigt: "
        f"Gehalt {'ja' if execution.get('income_confirmed') else 'nein'}, "
        f"Fixkosten {'ja' if execution.get('fixed_costs_confirmed') else 'nein'}, "
        f"Sparrate {'ja' if execution.get('savings_confirmed') else 'nein'}",
        f"Nettovermoegen: {_eur(profile.get('net_worth'))}",
        f"Vermoegensentwicklung: {dev_line}",
        f"Rov.E Score: {score.get('clarity_score', 0)}/100, Rang {score.get('rank_name', '-')}",
        f"Schwaechster Score-Baustein: {weakest or 'unklar'}",
        f"Groesste Ausgabenkategorie: {strongest.get('category', 'keine')} ({_eur(strongest.get('total'))})",
        f"Groesste Einzelbuchung: {biggest.get('merchant', 'keine')} ({_eur(biggest.get('amount'))})",
        f"Ziel: {goal.get('description', 'kein Ziel')} ueber {_eur(goal.get('target_amount'))}, "
        f"{round(goal.get('progress_percent') or 0)}% erreicht, {goal_eta}",
    ]

    budget = pages.get("budget") or {}
    if budget.get("has_budgets"):
        lines.append("Gemeinsam gesetzte Monatsbudgets (Limit vs. tatsaechlich ausgegeben):")
        for it in budget.get("items", []):
            pct = it.get("pct_used")
            pct_txt = f"{round(pct)}%" if pct is not None else "-"
            state = "UEBERZOGEN" if it.get("over") else "im Rahmen"
            lines.append(
                f"  - {it.get('category')}: Budget {_eur(it.get('limit'))}, "
                f"ausgegeben {_eur(it.get('used'))} ({pct_txt} genutzt, {state})"
            )
        adh = budget.get("adherence_pct")
        adh_txt = f"{round(adh)}%" if adh is not None else "-"
        state = "im Plan" if budget.get("on_track") else "ueber Plan"
        lines.append(
            f"Budget-Treue gesamt: {_eur(budget.get('total_used'))} von "
            f"{_eur(budget.get('total_limit'))} ({adh_txt}) -> {state}"
        )
    else:
        lines.append("Noch keine gemeinsam gesetzten Budgets aktiv.")

    return "\n".join(lines)


SYSTEM_PROMPT = (
    "Du bist Rov.E, ein ruhiger, ehrlicher und praeziser persoenlicher Finanzmentor. "
    "Du schreibst die individuellen Textbausteine fuer den monatlichen Finanzreport eines Nutzers."
)

INSTRUCTIONS = """Formuliere kurze, persoenliche Textbausteine fuer diesen Report.

Ton: ruhig, klar, ermutigend aber ehrlich - wie ein guter Mentor, der die Zahlen dieses
Menschen wirklich gesehen hat. Kein Marketing-Sprech, keine Floskeln, keine Emojis,
keine Anrede-Floskeln wie "Hallo". Deutsch, Du-Form.

Wichtige Regeln:
- Beziehe dich konkret auf das Verhalten GENAU DIESES Nutzers (die Datenlage unten).
  Zwei Nutzer mit unterschiedlichem Verhalten muessen klar unterschiedliche Texte bekommen.
- Erfinde KEINE Zahlen. Nutze nur Werte aus der Datenlage. Fehlt ein Wert oder ist 0,
  formuliere ohne ihn (nie "0 EUR" o. ae. erwaehnen).
- Zeitspannen IMMER in Jahren und Monaten angeben, genau wie in der Datenlage
  (z.B. "rund 52 Jahre" oder "3 Jahre und 2 Monate"). NIEMALS in reine Monate
  umrechnen (also nicht "629 Monate").
- Sei konkret und ehrlich. Wenn eine Zahl zeigt, wie weit oder nah etwas ist
  (z.B. eine sehr lange Zeitspanne bis zum Ziel), nenne sie ruhig - beschoenige nichts,
  bleib aber sachlich und nie entmutigend.
- BUDGETS: Wenn gemeinsam gesetzte Budgets aktiv sind, beziehe dich konkret darauf -
  haelt der Nutzer die gemeinsam gesetzten Budgets ein (folgt er dem Plan) oder nicht?
  Nutze die Budget-Treue statt allgemeiner Ausgaben-Tipps.
- Erfinde NIEMALS Budget-Grenzen (z.B. "Halte Lebensmittel unter 100 EUR"). Nenne nur
  Budgets, die in der Datenlage stehen. Sind KEINE Budgets aktiv, gib keine erfundenen
  Budget-Grenzen aus - beziehe dich dann auf das reale Ausgabeverhalten.
- Jedes Feld: 1-2 kurze Saetze, hoechstens ca. 140 Zeichen.
- "focus" besonders knapp halten: ein kurzer Satz, hoechstens ca. 75 Zeichen.

Bedeutung der Felder:
- development: Ein Satz zur Vermoegensentwicklung / zum Gesamtbild dieses Monats.
- best_decision: Die staerkste finanzielle Handlung dieses Nutzers diesen Monat, anerkennend benannt.
- focus: Der EINE Punkt, auf den er naechsten Monat schauen sollte.
- goal_honest: Ehrliche, konkrete Einschaetzung, wie realistisch/nah sein Ziel ist -
  eine grosse Distanz oder lange Zeitspanne klar benennen, nicht wegreden.
- goal_lever: Der konkrete Hebel, der ihn schneller ans Ziel bringt.
- recap_good: Was diesen Monat wirklich gut lief.
- recap_attention: Was Aufmerksamkeit braucht (ehrlich, nicht alarmistisch).
- recap_lever: Der wichtigste naechste Schritt.

Antworte AUSSCHLIESSLICH mit einem JSON-Objekt mit exakt diesen Keys:
development, best_decision, focus, goal_honest, goal_lever, recap_good, recap_attention, recap_lever

Datenlage:
"""


def generate_ai_narratives(data: dict) -> dict:
    """
    Liefert ein Dict mit personalisierten Textbausteinen (Keys aus ALLOWED_FIELDS).
    Bei fehlendem Key, deaktivierter KI oder jedem Fehler: leeres Dict -> Formel-Fallback.
    """
    if not _enabled():
        return {}

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        # Lokal / ohne Key: still auf Formel-Texte zurueckfallen, kein Netzaufruf.
        return {}

    try:
        import openai
    except ImportError:
        logger.warning("openai nicht installiert - Report nutzt Formel-Texte.")
        return {}

    try:
        if not getattr(openai, "api_key", None):
            openai.api_key = api_key

        prompt = INSTRUCTIONS + _build_signals(data)
        res = openai.chat.completions.create(
            model=AI_TEXT_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.6,
            max_tokens=500,
            timeout=REQUEST_TIMEOUT,
        )
        raw = json.loads(res.choices[0].message.content.strip())
    except Exception as e:
        logger.warning("KI-Report-Texte fehlgeschlagen (%s) - nutze Formel-Texte.", type(e).__name__)
        return {}

    # Nur erlaubte, gueltige Felder uebernehmen. Jedes ungueltige Feld faellt einzeln zurueck.
    out = {}
    for key in ALLOWED_FIELDS:
        value = raw.get(key)
        if isinstance(value, str):
            value = value.strip()
            if 0 < len(value) <= MAX_FIELD_LEN:
                out[key] = value
    return out
