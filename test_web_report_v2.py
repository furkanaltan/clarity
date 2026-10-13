import copy
import html
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from report_html_renderer import _pdf_design_details, _pdf_monthly_recap, _render_hell_pages
from report_story_v2 import build_report_story_v2
from rove_web_report_renderer import (
    build_render_context, data_count_span, render_template, standalone_script, web_story_details,
    web_euro_spacing,
)
from test_report_render_v2 import july_final_sample_payload, legacy_archive_payload


TEMPLATE = Path(__file__).parent / "report_templates" / "rove_web_report.html"


class WebReportV2Tests(unittest.TestCase):
    def setUp(self):
        self.data = july_final_sample_payload()
        self.context = build_render_context(self.data)
        self.details = web_story_details(self.data, self.context)

    def render(self):
        return render_template(TEMPLATE.read_text(encoding="utf-8"), self.data)

    def test_cashflow_reuses_confirmed_metric_without_recalculation(self):
        items = {item["key"]: item for item in self.details["web_cashflow"]["items"]}
        for key, value in (("income", "4000"), ("fixed_costs", "1500"),
                           ("consumption", "1856.54"), ("net", "143.46")):
            self.assertTrue(items[key]["available"])
            self.assertIn(f'data-count="{value}"', items[key]["span"])
        self.assertEqual(items["net"]["label"], "Verbleibend nach Sparen")
        self.assertIn("500 €", self.details["web_cashflow"]["note"])

    def test_unconfirmed_savings_does_not_expose_a_remaining_amount(self):
        self.data["report_truth"]["savings"]["confirmed"] = False
        self.data["report_story_v2"] = build_report_story_v2(self.data)
        detail = web_story_details(self.data, build_render_context(self.data))
        net = next(item for item in detail["web_cashflow"]["items"] if item["key"] == "net")
        self.assertFalse(net["available"])
        self.assertEqual(net["span"], "—")
        self.assertIn("nicht vollständig bestätigt", detail["web_cashflow"]["note"])

    def test_missing_income_is_not_presented_as_zero(self):
        self.data["report_truth"]["income"] = {"source": "unavailable", "amount": None}
        detail = web_story_details(self.data, build_render_context(self.data))
        income = detail["web_cashflow"]["items"][0]
        self.assertEqual(income["span"], "—")
        self.assertEqual(income["status"], "Nicht verfügbar")

    def test_wealth_shares_come_from_existing_story(self):
        for item, original in zip(self.details["web_allocation"], self.context["report"]["allocation"]):
            self.assertEqual(item["share_raw"], original["share_raw"])
            self.assertEqual(item["share_span"], data_count_span(original["share_raw"], 1))

    def test_category_count_average_and_merchant_category_are_visible(self):
        doc = self.render()
        self.assertIn("4 Buchungen · Ø 245,75\u00a0€", doc)
        self.assertIn("Shopping</div>", doc)
        self.assertIn("Elektronikmarkt", doc)

    def test_short_category_bars_keep_share_legible_without_changing_width(self):
        doc = self.render()
        self.assertIn('<span class="report-category-share">1 % des Konsums</span>', doc)
        self.assertIn('data-grow="4.0%"', doc)
        self.assertIn(".report-category-share { white-space: nowrap; }", doc)

    def test_missing_category_booking_metadata_is_not_invented(self):
        for item in self.data["report_truth"]["expenses"]["categories"]:
            item.pop("transaction_count", None)
            item.pop("avg_transaction", None)
        self.data["report_story_v2"] = build_report_story_v2(self.data)
        doc = self.render()
        self.assertIn("Buchungsdetails nicht verfügbar", doc)
        self.assertNotIn("0 Buchungen", doc)
        self.assertNotIn("Ø 0 €", doc)

    def test_missing_category_average_does_not_hide_known_count(self):
        for item in self.data["report_truth"]["expenses"]["categories"]:
            item.pop("avg_transaction", None)
        self.data["report_story_v2"] = build_report_story_v2(self.data)
        doc = self.render()
        self.assertIn("4 Buchungen", doc)
        self.assertNotIn("Ø 0 €", doc)

    def test_largest_expense_matches_pdf_metadata(self):
        self.assertEqual(self.details["web_largest_expense"],
                         _pdf_design_details(self.data, self.context)["pdf_largest_expense"])
        self.assertIn("500 €", self.details["web_largest_expense"]["amount"])
        self.assertEqual(self.details["web_largest_expense"]["date"], "18.07.2026")
        self.assertIn("Größte einzelne Konsumausgabe", self.render())

    def test_missing_largest_expense_has_honest_fallback(self):
        self.data["report_truth"]["expenses"]["largest_expense"] = None
        doc = self.render()
        self.assertIn("keine größte einzelne Konsumausgabe belegt", doc)
        self.assertNotIn("None", doc)

    def test_largest_expense_escapes_metadata_and_rejects_foreign_month_date(self):
        expense = self.data["report_truth"]["expenses"]["largest_expense"]
        expense.update(merchant="<script>bad()</script>", created_at="2026-08-18")
        doc = self.render()
        self.assertNotIn("<script>bad()</script>", doc)
        self.assertNotIn("18.08.2026", doc)

    def test_milestones_use_pdf_localization_and_same_progress_basis(self):
        expected = _pdf_design_details(self.data, self.context)["pdf_milestones"]
        self.assertEqual(self.details["web_milestones"], expected)
        self.assertEqual(expected["progress_raw"], self.context["milestone_pct_raw"])
        for badge in expected["badges"]:
            self.assertIn(badge["label"], self.render())
            if badge["date"]:
                self.assertRegex(badge["date"], r"^\d{2}\.\d{2}\.\d{4}$")

    def test_recap_uses_exact_pdf_evidence_not_a_second_insight_engine(self):
        self.assertEqual(self.details["web_recap"], _pdf_monthly_recap(self.data, self.context))
        doc = self.render()
        for label in ("Das lief gut", "Das fällt auf", "Dein nächster Hebel"):
            self.assertIn(label, doc)
        for block in self.details["web_recap"].values():
            for fact in block["facts"]:
                self.assertIn(web_euro_spacing(fact), doc)
        self.assertIn("keine nachgewiesene Ursache", html.unescape(doc))

    def test_recap_missing_data_does_not_invent_progress(self):
        self.data["report_truth"]["savings"]["confirmed"] = False
        self.data["report_truth"]["wealth_period"] = {"available": False}
        self.data["report_truth"]["budget"] = {"has_budgets": False}
        self.data["report_story_v2"]["insight_engine"]["candidates"] = []
        details = web_story_details(self.data, build_render_context(self.data))
        self.assertIn("noch nicht belegt", details["web_recap"]["good"]["title"])
        self.assertIn("kein konkreter Verbesserungshebel", details["web_recap"]["lever"]["facts"][0])

    def test_render_does_not_mutate_snapshot_or_pdf(self):
        original = copy.deepcopy(self.data)
        pdf_before = _render_hell_pages(self.data)
        self.render()
        self.assertEqual(self.data, original)
        self.assertEqual(_render_hell_pages(self.data), pdf_before)

    def test_legacy_snapshot_keeps_its_existing_recap(self):
        data = legacy_archive_payload()
        context = build_render_context(data)
        details = web_story_details(data, context)
        self.assertEqual(details["web_recap"]["good"]["facts"],
                         [context["recap_good_text"] or "Für diesen historischen Monat nicht belegt."])
        self.assertEqual(len(re.findall(r"<section\b", render_template(TEMPLATE.read_text(), data))), 10)

    def test_dark_design_and_existing_motion_hooks_are_preserved(self):
        doc = self.render()
        self.assertEqual(len(re.findall(r"<section\b", doc)), 10)
        for marker in ("#08090B", "#3BA7FF", "Hanken Grotesk", "Newsreader", "data-grow",
                       "data-ring", "data-reveal", "rove-cover-cameo", "rove-report-assistant"):
            self.assertIn(marker, doc)
        self.assertNotIn("extends DCLogic", doc)
        self.assertEqual(doc.count("function initialize()"), 1)

    def test_requested_headings_and_natural_copy_keep_financial_facts(self):
        doc = self.render()
        for label in ("Hier hast du am meisten ausgegeben.", "Dein Rov.E Score im Überblick.",
                      "Dein Monatsrückblick.", "Dein aktueller Stand"):
            self.assertIn(label, doc)
        for old in ("Deine größten Händler.", "So steht dein Monat da.",
                    "Ehrlich gesagt", "Ehrlich zusammengefasst"):
            self.assertNotIn(old, doc)
        for key in ("goal_honest_text", "goal_honest_subtext", "plan_step1_title",
                    "plan_step2_title", "plan_step3_title"):
            self.assertIn(web_euro_spacing(self.context[key]), doc)

    def test_chapter_and_card_hierarchy_uses_shared_preserved_fonts(self):
        doc = self.render()
        self.assertEqual(doc.count('class="report-chapter-label"'), 9)
        self.assertEqual(doc.count('class="report-chapter-title"'), 8)
        self.assertIn(".report-chapter-title { font-family: 'Newsreader', serif;", doc)
        self.assertIn(".report-label { font-size: 11px; font-weight: 600;", doc)
        self.assertIn('class="report-chapter-label" style="color:#D8B66A;"', doc)
        self.assertEqual(doc.count('class="report-card-title"'), 6)

    def test_reserve_cards_have_no_fixed_height_and_keep_all_information(self):
        doc = self.render().replace("\u00a0", " ")
        self.assertIn('data-reserve="buffer"', doc)
        self.assertIn('data-reserve="debt"', doc)
        self.assertNotIn("min-height:218px", doc)
        self.assertIn("align-items: start; gap: 12px;", doc)
        for key in ("buffer_months_amount", "buffer_cash_amount", "buffer_change_text",
                    "buffer_target_amount", "buffer_gap_amount"):
            self.assertIn(self.context[key], doc)

    def test_single_reserve_and_missing_basis_do_not_create_empty_debt_card(self):
        self.data["report_truth"]["debts"].update(consumer_current=0, mortgage_current=0)
        self.data["report_truth"]["buffer"].update(monthly_basis=None, covered_months=None)
        self.data["report_story_v2"] = build_report_story_v2(self.data)
        doc = self.render()
        self.assertIn('data-reserve="buffer"', doc)
        self.assertNotIn('data-reserve="debt"', doc)
        self.assertIn(web_euro_spacing(build_render_context(self.data)["buffer_coverage_text"]), doc)
        self.assertNotIn("min-height:218px", doc)

    def test_currency_spacing_is_presentation_only_and_preserves_escaping(self):
        for text, expected in (("200 €", "200\u00a0€"), ("-1.234,56 €", "-1.234,56\u00a0€"),
                               ("+500 €/Monat", "+500\u00a0€/Monat"), ("0 €", "0\u00a0€"),
                               ("5 %", "5 %"), ("kein Betrag €", "kein Betrag €"),
                               ("200\u00a0€", "200\u00a0€"), (None, None), (143.46, 143.46)):
            with self.subTest(text=text):
                self.assertEqual(web_euro_spacing(text), expected)
        self.data["report_truth"]["expenses"]["largest_expense"]["merchant"] = "<script>200 €</script>"
        doc = self.render()
        self.assertIn("&lt;script&gt;200\u00a0€&lt;/script&gt;", doc)
        self.assertNotIn("<script>200", doc)
        self.assertNotRegex(doc, r"\d €")
        self.assertIn('data-count="143.46"', doc)

    def test_final_plan_currency_is_not_orphaned_and_values_are_unchanged(self):
        doc = self.render()
        plan = doc[doc.index('data-screen-label="10 Nächster Monat"'):]
        for key in ("plan_step1_title", "plan_step1_sub", "plan_step1_impact",
                    "plan_step2_title", "plan_step2_sub", "plan_step2_impact",
                    "plan_step3_title", "plan_step3_sub", "plan_step3_impact"):
            self.assertIn(web_euro_spacing(self.context[key]), plan)
        self.assertNotRegex(plan, r"\d €")
        self.assertEqual(plan.count('class="report-plan-card"'), 3)

    def test_count_spans_keep_money_cents_score_and_percentage_precision(self):
        for value, decimals, raw, label in ((1856.54, 0, "1856.54", "1.856,54"),
                                           (-143.46, 0, "-143.46", "-143,46"),
                                           (64, 0, "64", "64"), (51.04, 1, "51.0", "51,0")):
            span = data_count_span(value, decimals)
            self.assertIn(f'data-count="{raw}"', span)
            self.assertIn(f'>{label}</span>', span)

    def test_count_spans_reject_invalid_financial_values(self):
        for value in (None, True, "bad", float("nan"), float("inf")):
            self.assertEqual(data_count_span(value), "—")

    @unittest.skipUnless(shutil.which("node"), "Node is required for animation runtime tests")
    def test_actual_animation_runtime_zero_exact_end_reentry_and_reduced_motion(self):
        harness = r'''
const vm = require('node:vm'), assert = require('node:assert/strict');
let source = '';
process.stdin.on('data', chunk => { source += chunk; });
process.stdin.on('end', () => {
  function run(reduced = false, observerAvailable = true) {
    const values = [['1856.54','2','1.856,54'], ['-143.46','2','-143,46'],
                    ['64','0','64'], ['51.0','1','51,0'], ['0','0','0'],
                    ['invalid','0','—'], ['','0','—'], ['2','8','—']];
    const elements = values.map(([value, decimals, text]) => ({
      textContent: text, style: {},
      getAttribute: key => key === 'data-count' ? value : decimals,
      hasAttribute: key => key === 'data-count'
    }));
    let frames = [], observer;
    class IO {
      constructor(callback) { this.callback = callback; observer = this; }
      observe() {} unobserve() {} disconnect() {}
    }
    const document = {body: {getAttribute: () => null},
      querySelector: () => null,
      querySelectorAll: key => key === '[data-count]' ? elements : []};
    const window = {matchMedia: () => ({matches: reduced}),
                    IntersectionObserver: observerAvailable ? IO : undefined};
    vm.runInNewContext(source, {document, window, IntersectionObserver: IO,
      requestAnimationFrame: fn => frames.push(fn), Intl, Date, Number, Math, setTimeout});
    if (reduced || !observerAvailable) {
      assert.deepEqual(elements.map(el => el.textContent), values.map(v => v[2]));
      assert.equal(frames.length, 0); return;
    }
    assert.deepEqual(elements.map(el => el.textContent), ['0,00','0,00','0','0,0','0','—','—','—']);
    const entries = elements.slice(0, 5).map(target => ({target, isIntersecting: true}));
    observer.callback(entries);
    for (const time of [0, 750, 1500]) {
      const pending = frames; frames = []; pending.forEach(fn => fn(time));
      if (time === 0) assert.equal(elements[0].textContent, '0,00');
      if (time === 750) assert.notEqual(elements[0].textContent, '1.856,54');
    }
    assert.deepEqual(elements.map(el => el.textContent), values.map(v => v[2]));
    assert.equal(frames.length, 0);
    observer.callback(entries);
    assert.equal(frames.length, 0);
    assert.deepEqual(elements.map(el => el.textContent), values.map(v => v[2]));
  }
  run(); run(true); run(false, false);
  console.log('ANIMATION_RUNTIME=PASS');
});
'''
        script = standalone_script().removeprefix("<script>").removesuffix("</script>").strip()
        result = subprocess.run([shutil.which("node"), "-e", harness], input=script,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ANIMATION_RUNTIME=PASS", result.stdout)


if __name__ == "__main__":
    unittest.main()
