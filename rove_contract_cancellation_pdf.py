"""Private, on-demand cancellation evidence PDF; no persisted/public artifacts."""
from io import BytesIO
from datetime import date, datetime, timezone
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo


EVENT_LABELS = {
    'case_created': 'Vorgang angelegt', 'review_required': 'Prüfung erforderlich',
    'review_updated': 'Angaben aktualisiert', 'review_invalidated': 'Neue Prüfung erforderlich',
    'user_confirmed': 'Kündigungstext bestätigt', 'ready_to_send': 'Versand vorbereitet',
    'send_requested': 'Versand beauftragt', 'sending': 'Versand gestartet',
    'sent': 'Maildienst hat Nachricht angenommen', 'delivery_recorded': 'Zugestellt',
    'response_received': 'Erhaltene Antwort dokumentiert', 'termination_confirmed': 'Kündigungsergebnis bestätigt',
    'failed': 'Vorgang fehlgeschlagen', 'retry': 'Wiederholung ausdrücklich beauftragt',
    'cancelled': 'Vorbereitung abgebrochen', 'follow_up_due': 'Nachfassen nötig',
    'follow_up_prepared': 'Nachfrage vorbereitet, nicht versendet', 'manual_review_required': 'Manuelle Prüfung nötig',
    'brevo_delivery_sent': 'Gesendet', 'brevo_delivery_delivered': 'Zugestellt',
    'brevo_delivery_deferred': 'Zustellung verzögert', 'brevo_delivery_soft_bounce': 'Zustellung verzögert',
    'brevo_delivery_hard_bounce': 'Zustellung fehlgeschlagen', 'brevo_delivery_blocked': 'Zustellung fehlgeschlagen',
    'brevo_delivery_invalid': 'Zustellung fehlgeschlagen', 'brevo_delivery_error': 'Zustellung fehlgeschlagen',
}
BERLIN = ZoneInfo('Europe/Berlin')


def format_timestamp(value):
    if not value:
        return 'Nicht dokumentiert'
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        return str(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(BERLIN).strftime('%d.%m.%Y, %H:%M Uhr')


def format_date(value):
    if not value:
        return 'Nicht mitgeteilt'
    try:
        return date.fromisoformat(str(value)).strftime('%d.%m.%Y')
    except ValueError:
        return str(value)


def render_cancellation_pdf(case_file):
    # Use the existing reportlab/font infrastructure, without changing monthly reports.
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfgen.canvas import Canvas
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether
    from rove_pdf_report_renderer import register_fonts, font

    register_fonts()
    regular, bold = font('RoveSans'), font('RoveSans-Bold')
    ink, muted, accent = colors.HexColor('#17212F'), colors.HexColor('#596675'), colors.HexColor('#1969A7')
    styles = {
        'body': ParagraphStyle('VksBody', fontName=regular, fontSize=10, leading=15, textColor=ink,
                               spaceAfter=8, splitLongWords=True, alignment=TA_LEFT),
        'small': ParagraphStyle('VksSmall', fontName=regular, fontSize=8.5, leading=12, textColor=muted,
                                spaceAfter=6, splitLongWords=True),
        'title': ParagraphStyle('VksTitle', fontName=bold, fontSize=26, leading=32, textColor=ink, spaceAfter=12),
        'heading': ParagraphStyle('VksHeading', fontName=bold, fontSize=13, leading=18, textColor=accent,
                                  spaceBefore=16, spaceAfter=8, keepWithNext=True),
        'cell': ParagraphStyle('VksCell', fontName=regular, fontSize=8.5, leading=11, textColor=muted,
                               spaceAfter=0, splitLongWords=True),
    }
    story = []

    def text(value, style='body'):
        content = escape(str(value if value is not None and value != '' else 'Nicht dokumentiert'))
        return Paragraph(content.replace('\n', '<br/>'), styles[style])

    def add(value, style='body'):
        story.append(text(value, style))

    add('Kündigungsakte', 'title')
    add('Dokumentierter Stand dieses Vorgangs. Versand ist keine Kündigungsbestätigung.', 'small')
    add(f"Erstellt am: {format_date(case_file['generated_on'])} · Vorgangs-ID: {case_file['case_reference']}", 'small')
    add('Vertrag', 'heading')
    contract_rows = []
    if case_file['provider']:
        contract_rows.append(('Anbieter', case_file['provider']))
    if case_file['contract'] and case_file['contract'] != case_file['provider']:
        contract_rows.append(('Vertrag', case_file['contract']))
    elif not case_file['provider']:
        contract_rows.append(('Vertrag', case_file['contract']))
    contract_rows.extend((('Vertrags-/Kundennummer', case_file['contract_reference']),
                          ('Status', case_file['status_label'])))
    for label, value in contract_rows:
        add(f"{label}: {value or 'Nicht dokumentiert'}")
    add('Providerbestätigung', 'heading')
    if case_file['confirmed_at']:
        add(f"Kündigungsergebnis am {format_timestamp(case_file['confirmed_at'])} ausdrücklich vom Nutzer bestätigt.")
        add('Grundlage: eine vom Nutzer dokumentierte erhaltene Anbieterantwort; nicht automatisch verifiziert.', 'small')
    else:
        add('Keine bestätigte Kündigung dokumentiert.')
    add(f"Bestätigtes Enddatum: {format_date(case_file['confirmed_end_date'])}")
    add('Chronologie', 'heading')
    actors = {'user': 'Nutzer', 'system': 'Rov.E', 'transport': 'Maildienst', 'legacy': 'Ältere Dokumentation'}
    rows = [[text('Zeitpunkt (Europe/Berlin)', 'cell'), text('Dokumentierter Schritt', 'cell')]]
    for event in case_file['timeline']:
        label = EVENT_LABELS.get(event['event_type'], 'Dokumentierter Statuswechsel')
        actor = actors.get(event['actor'], 'Dokumentation')
        rows.append([text(format_timestamp(event['occurred_at']), 'cell'), text(f'{label}\n{actor}', 'cell')])
    table = Table(rows, colWidths=[126, A4[0] - 214], repeatRows=1, hAlign='LEFT')
    table.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'),
                               ('LEFTPADDING', (0, 0), (-1, -1), 0),
                               ('RIGHTPADDING', (0, 0), (-1, -1), 12),
                               ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
                               ('LINEBELOW', (0, 0), (-1, 0), .5, colors.HexColor('#D9E0E7'))]))
    story.append(table)
    notice_start = len(story)
    add('Kündigungsschreiben', 'heading')
    add(f"Nutzerbestätigung: {format_timestamp(case_file['user_confirmed_at'])}", 'small')
    if case_file['user_confirmed_at'] and not case_file['notice_integrity']:
        add('Die gespeicherte Textversion stimmt nicht mit der Bestätigung überein. Manuelle Prüfung erforderlich.', 'small')
    add(case_file['notice_text'])
    if len(case_file['notice_text']) < 1500:
        story[notice_start:] = [KeepTogether(story[notice_start:])]
    add('Versandnachweise', 'heading')
    outgoing = [message for message in case_file['messages'] if message['direction'] == 'outbound']
    if not outgoing:
        add('Kein Versand dokumentiert.')
    transport_labels = {'accepted': 'Vom Maildienst angenommen', 'delivered': 'Zugestellt',
                        'deferred': 'Zustellung verzögert', 'soft_bounce': 'Zustellung verzögert',
                        'hard_bounce': 'Zustellung fehlgeschlagen', 'blocked': 'Zustellung fehlgeschlagen',
                        'invalid': 'Zustellung fehlgeschlagen', 'error': 'Zustellung fehlgeschlagen',
                        'sending': 'Ausgang noch unklar', 'unknown': 'Ausgang unklar', 'rejected': 'Versand abgelehnt'}
    for index, message in enumerate(outgoing, 1):
        add(f"Versuch {index}: {transport_labels.get(message['transport_status'], 'Nicht belegt')}")
        for label, value in (('Absender', message['sender']), ('Empfänger', message['recipient']),
                             ('Reply-To', message['reply_to']), ('Betreff', message['subject']),
                             ('Transport-ID', message['provider_message_id']),
                             ('Angenommen', format_timestamp(message['sent_at']) if message['sent_at'] else None),
                             ('Zugestellt', format_timestamp(message['delivered_at']) if message['delivered_at'] else None)):
            if value:
                add(f"{label}: {value}", 'small')
        add('Text-Prüfsumme (SHA-256): ' + message['body_sha256'], 'small')
    add('Erhaltene Anbieterantworten', 'heading')
    incoming = [message for message in case_file['messages'] if message['direction'] == 'inbound']
    if not incoming:
        add('Keine erhaltene Antwort dokumentiert.')
    for index, message in enumerate(incoming, 1):
        add(f"Antwort {index} · vom Nutzer dokumentiert, nicht automatisch verifiziert", 'small')
        add(f"Absender: {message['sender']}\nEmpfangen: {format_timestamp(message['received_at'])}\nBetreff: {message['subject']}", 'small')
        add(message['body_text'])
    followup_start = len(story)
    add('Nachfragen', 'heading')
    if not case_file['followups']:
        add('Keine Nachfrage vorbereitet oder versendet.')
    for followup in case_file['followups']:
        add(f"Vorbereitet am {format_timestamp(followup['created_at'])}. Nicht durch Rov.E versendet.", 'small')
        add(followup['body_text'])
    if sum(len(followup['body_text']) for followup in case_file['followups']) < 1500:
        story[followup_start:] = [KeepTogether(story[followup_start:])]
    story.append(Spacer(1, 10))
    add('Der Vertrag bleibt dokumentiert. Diese Akte verändert weder den Monatsplan noch Finanzwerte. '
        'Sie dokumentiert gespeicherte Angaben und ersetzt keine rechtliche Prüfung.', 'small')

    payload = BytesIO()
    document = SimpleDocTemplate(payload, pagesize=A4, rightMargin=44, leftMargin=44,
                                 topMargin=40, bottomMargin=44, title='Kündigungsakte', author='Rov.E', invariant=1)

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont(regular, 8)
        canvas.setFillColor(muted)
        canvas.drawString(44, 23, 'Rov.E · Kündigungsakte · privat')
        canvas.restoreState()

    class NumberedCanvas(Canvas):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._saved_page_states = []

        def showPage(self):
            self._saved_page_states.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total_pages = len(self._saved_page_states)
            for state in self._saved_page_states:
                self.__dict__.update(state)
                self.saveState()
                self.setFont(regular, 8)
                self.setFillColor(muted)
                self.drawRightString(A4[0] - 44, 23, f'Seite {self._pageNumber} von {total_pages}')
                self.restoreState()
                Canvas.showPage(self)
            Canvas.save(self)

    def private_canvas(*args, **kwargs):
        canvas = NumberedCanvas(*args, **kwargs)
        # Omit synthetic invariant-epoch metadata; evidence dates appear explicitly in the document.
        canvas.setDateFormatter(lambda *parts: '')
        return canvas

    document.build(story, onFirstPage=footer, onLaterPages=footer, canvasmaker=private_canvas)
    return payload.getvalue()
