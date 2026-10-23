import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parent
FRONTEND = ROOT / "frontend" / "index.html"


class AdminRefreshButtonUxTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = FRONTEND.read_text(encoding="utf-8")

    def test_refresh_button_uses_scoped_dark_material(self):
        self.assertIn(
            '#adminsheet .admin-refresh-btn{display:block;width:100%;margin-top:14px;',
            self.source,
        )
        self.assertIn(
            "border-color:rgba(211,221,229,.2);border-radius:14px;"
            "background:linear-gradient(145deg,rgba(255,255,255,.09),rgba(255,255,255,.025))",
            self.source,
        )
        self.assertNotIn(
            '<button class="vd-btn" id="adminRefresh" style=',
            self.source,
        )

    def test_refresh_button_has_light_theme_material(self):
        self.assertIn(
            ':root[data-theme="light"] #adminsheet .admin-refresh-btn{'
            "border-color:rgba(67,90,108,.15);background:linear-gradient(145deg,#fff,#edf3f6)",
            self.source,
        )

    def test_refresh_button_keeps_admin_action_path(self):
        self.assertIn(
            '<button class="vd-btn admin-refresh-btn" id="adminRefresh">Aktualisieren</button>',
            self.source,
        )
        self.assertIn(
            'if(e.target.closest("#adminRefresh")){ loadAdminOverview(); buzz(); return; }',
            self.source,
        )
        self.assertIn('apiFetch("/v1/admin/overview"', self.source)
        self.assertIn('if(!DATA.identity?.isAdmin || !apiReady()){ closeSheet(); return; }', self.source)


if __name__ == "__main__":
    unittest.main()
