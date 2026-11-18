from django.test import SimpleTestCase,TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from .models import Scan
from .forms import WebsiteScanForm
from .accessibility import (scan_website, ScanError, AXE_CORE_URL, is_safe_browser_request,)
from .pdf_reports import generate_scan_pdf
from playwright.sync_api import sync_playwright
from unittest.mock import patch, MagicMock
from concurrent.futures import ThreadPoolExecutor

class WebsiteScanFormTests(SimpleTestCase):

    def test_valid_https_url(self):
        form = WebsiteScanForm(
            data={"url": "https://example.com"}
        )
        self.assertTrue(form.is_valid())

    def test_valid_http_url(self):
        form = WebsiteScanForm(
            data={"url": "http://example.com"}
        )
        self.assertTrue(form.is_valid())

    def test_rejects_ftp_url(self):
        form = WebsiteScanForm(
            data={"url": "ftp://example.com"}
        )
        self.assertFalse(form.is_valid())

    def test_rejects_localhost(self):
        form = WebsiteScanForm(
            data={"url": "http://localhost:8000"}
        )
        self.assertFalse(form.is_valid())

    def test_rejects_loopback_ip(self):
        form = WebsiteScanForm(
            data={"url": "http://127.0.0.1"}
        )
        self.assertFalse(form.is_valid())

    def test_rejects_empty_url(self):
        form = WebsiteScanForm(data={"url": ""})
        self.assertFalse(form.is_valid())

class ScanOwnershipTests(TestCase):

    def setUp(self):
        User = get_user_model()

        self.user_a = User.objects.create_user(
            username="test_user_a",
            password="TestPassword123!"
        )

        self.user_b = User.objects.create_user(
            username="test_user_b",
            password="TestPassword456!"
        )

        self.sample_results = {
            "summary": {
                "critical": 0,
                "serious": 0,
                "moderate": 0,
                "minor": 0,
                "passed": 1,
                "manual_review": 0,
            },
            "violations": [],
            "passes": [],
            "incomplete": [],
        }

        self.scan_a = Scan.objects.create(
            user=self.user_a,
            url="https://example.com",
            status="completed",
            results=self.sample_results,
        )

        self.scan_b = Scan.objects.create(
            user=self.user_b,
            url="https://example.org",
            status="completed",
            results=self.sample_results,
        )

        self.guest_scan = Scan.objects.create(
            user=None,
            url="https://example.net",
            status="completed",
            results=self.sample_results,
        )

    def test_guest_cannot_access_scan_history(self):
        response = self.client.get(reverse("scan_history"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response.url)

    def test_user_can_view_own_scan(self):
        self.client.force_login(self.user_a)
        response = self.client.get(reverse("scan_results", args=[self.scan_a.id]))
        self.assertEqual(response.status_code, 200)

    def test_user_cannot_view_another_users_scan(self):
        self.client.force_login(self.user_a)
        response = self.client.get(reverse("scan_results", args=[self.scan_b.id]))
        self.assertEqual(response.status_code, 404)

    def test_user_cannot_download_another_users_pdf(self):
        self.client.force_login(self.user_a)
        response = self.client.get(reverse("download_scan_pdf", args=[self.scan_b.id]))
        self.assertEqual(response.status_code, 404)

    def test_guest_cannot_view_unowned_scan(self):
        response = self.client.get(reverse("scan_results", args=[self.guest_scan.id]))
        self.assertEqual(response.status_code, 404)

    def test_guest_can_view_session_scan(self):
        session = self.client.session
        session["guest_scan_ids"] = [self.guest_scan.id]
        session.save()
        response = self.client.get(reverse("scan_results",args=[self.guest_scan.id]))
        self.assertEqual(response.status_code, 200)

    def test_guest_can_download_session_pdf(self):
        session = self.client.session
        session["guest_scan_ids"] = [self.guest_scan.id]
        session.save()
        response = self.client.get(reverse("download_scan_pdf",args=[self.guest_scan.id]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"],"application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF"))

    def test_history_contains_only_own_scans(self):
        self.client.force_login(self.user_a)
        response = self.client.get(reverse("scan_history"))
        self.assertEqual(response.status_code, 200)
        scans = list(response.context["scans"])
        self.assertIn(self.scan_a, scans)
        self.assertNotIn(self.scan_b, scans)
        self.assertNotIn(self.guest_scan, scans)

    def test_pdf_unavailable_for_failed_scan(self):
        self.scan_a.status = "failed"
        self.scan_a.save()
        self.client.force_login(self.user_a)
        response = self.client.get(reverse("download_scan_pdf", args=[self.scan_a.id]))
        self.assertEqual(response.status_code, 409)

    def test_pdf_download_rejects_post(self):
        self.client.force_login(self.user_a)
        response = self.client.post(reverse("download_scan_pdf", args=[self.scan_a.id]))
        self.assertEqual(response.status_code, 405)

class ScannerAndPDFTests(TestCase):
    def setUp(self):
        self.sample_axe_results = {
            "violations": [
                {
                    "id": "image-alt",
                    "impact": "critical",
                    "description": "Images need alternatives",
                    "help": "Images must have alternate text",
                    "helpUrl": "https://example.com/help",
                    "tags": ["wcag2a"],
                    "nodes": [
                        {
                            "target": ["img"],
                            "failureSummary": "Missing alt text",
                        }
                    ],
                },
                {
                    "id": "color-contrast",
                    "impact": "serious",
                    "description": "Low text contrast",
                    "help": "Improve color contrast",
                    "helpUrl": "https://example.com/help",
                    "tags": ["wcag2aa"],
                    "nodes": [],
                },
                {
                    "id": "label",
                    "impact": "moderate",
                    "description": "Missing form label",
                    "help": "Provide a label",
                    "helpUrl": "https://example.com/help",
                    "tags": ["wcag2a"],
                    "nodes": [],
                },
                {
                    "id": "region",
                    "impact": "minor",
                    "description": "Content outside landmarks",
                    "help": "Use landmarks",
                    "helpUrl": "https://example.com/help",
                    "tags": [],
                    "nodes": [],
                },
            ],
            "passes": [
                {
                    "id": "document-title",
                    "description": "Page has a title",
                    "nodes": [{}, {}],
                }
            ],
            "incomplete": [
                {
                    "id": "aria-hidden-focus",
                    "impact": "moderate",
                    "description": "Needs manual review",
                    "help": "Check hidden elements",
                    "helpUrl": "https://example.com/help",
                    "nodes": [{}],
                }
            ],
        }

    @patch("scanner.accessibility.sync_playwright")
    def test_scanner_processes_axe_results(self, mock_playwright):

        mock_manager = MagicMock()
        mock_playwright.return_value.__enter__.return_value = (mock_manager)
        mock_browser = mock_manager.chromium.launch.return_value
        mock_context = mock_browser.new_context.return_value
        mock_page = mock_context.new_page.return_value
        mock_page.content.return_value = "<html><body></body></html>"
        mock_browser.new_page.return_value = mock_page
        mock_page.evaluate.return_value = (self.sample_axe_results)

        results = scan_website("https://example.com")
        self.assertEqual(results["summary"]["critical"], 1)
        self.assertEqual(results["summary"]["serious"], 1)
        self.assertEqual(results["summary"]["moderate"], 1)
        self.assertEqual(results["summary"]["minor"], 1)
        self.assertEqual(results["summary"]["passed"], 1)
        self.assertEqual(results["summary"]["manual_review"], 1)
        self.assertEqual(len(results["violations"]), 4)
        self.assertEqual(len(results["passes"]), 1)
        self.assertEqual(len(results["incomplete"]), 1)
        self.assertEqual(results["violations"][0]["nodes"][0]["target"],["img"],)

        mock_browser.close.assert_called_once()

    def test_scanner_rejects_localhost(self):
        with self.assertRaises(ScanError):
            scan_website("http://localhost")

    def test_scanner_rejects_private_ip(self):
        with self.assertRaises(ScanError):
            scan_website("http://192.168.1.1")

    def test_pdf_generation(self):
        scan = Scan.objects.create(
            url="https://example.com",
            status="completed",
            results={
                "summary": {
                    "critical": 1,
                    "serious": 0,
                    "moderate": 0,
                    "minor": 0,
                    "passed": 1,
                    "manual_review": 0,
                },
                "violations": [
                    {
                        "id": "image-alt",
                        "impact": "critical",
                        "description": "Missing alt text",
                        "help": "Images must have alt text",
                        "help_url": "https://example.com/help",
                        "nodes": [
                            {
                                "target": ["img"],
                                "failure_summary": "Missing alt",
                            }
                        ],
                    }
                ],
                "passes": [
                    {
                        "id": "document-title",
                        "description": "Page has a title",
                        "nodes_count": 1,
                    }
                ],
                "incomplete": [],
            },
        )

        pdf_data = generate_scan_pdf(scan)
        self.assertIsInstance(pdf_data, bytes)
        self.assertTrue(pdf_data.startswith(b"%PDF"))
        self.assertGreater(len(pdf_data), 100)

    def test_pdf_download_headers(self):
        User = get_user_model()

        user = User.objects.create_user(
            username="pdf_test_user",
            password="TestPassword123!",
        )

        scan = Scan.objects.create(
            user=user,
            url="https://example.com",
            status="completed",
            results={
                "summary": {
                    "critical": 0,
                    "serious": 0,
                    "moderate": 0,
                    "minor": 0,
                    "passed": 0,
                    "manual_review": 0,
                },
                "violations": [],
                "passes": [],
                "incomplete": [],
            },
        )

        self.client.force_login(user)
        response = self.client.get(reverse("download_scan_pdf", args=[scan.id]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf",)
        self.assertIn("attachment;", response["Content-Disposition"],)
        self.assertIn(f"wcag_report_{scan.id}.pdf", response["Content-Disposition"],)
        self.assertEqual(response["X-Content-Type-Options"], "nosniff",)
        self.assertTrue(response.content.startswith(b"%PDF"))

class ApplicationAccessibilityTests(StaticLiveServerTestCase):

    def setUp(self):
        User = get_user_model()

        self.user = User.objects.create_user(
            username="accessibility_test_user",
            password="TestPassword123!",
        )

        self.scan = Scan.objects.create(
            user=self.user,
            url="https://example.com",
            status="completed",
            results={
                "summary": {
                    "critical": 0,
                    "serious": 0,
                    "moderate": 0,
                    "minor": 0,
                    "passed": 1,
                    "manual_review": 0,
                },
                "violations": [],
                "passes": [
                    {
                        "id": "document-title",
                        "description": "Document has a title",
                        "nodes_count": 1,
                    }
                ],
                "incomplete": [],
            },
        )

def check_accessibility(self, path, authenticated=False):
    url = self.live_server_url + path
    login_url = self.live_server_url + reverse("login")
    home_url = self.live_server_url + reverse("home")

    def run_browser_test():
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)

            try:
                context = browser.new_context()
                page = context.new_page()

                if authenticated:
                    page.goto(login_url)

                    page.locator(
                        'input[name="username"]'
                    ).fill("accessibility_test_user")

                    page.locator(
                        'input[name="password"]'
                    ).fill("TestPassword123!")

                    page.locator(
                        'button[type="submit"]'
                    ).click()

                    page.wait_for_url(home_url)

                page.goto(url, wait_until="domcontentloaded")

                # Load axe-core.
                page.add_script_tag(url=AXE_CORE_URL)

                return page.evaluate(
                    """
                    async () => {
                        return await axe.run(document, {
                            runOnly: {
                                type: "tag",
                                values: [
                                    "wcag2a",
                                    "wcag2aa",
                                    "wcag21a",
                                    "wcag21aa",
                                    "wcag22aa",
                                    "best-practice"
                                ]
                            }
                        });
                    }
                    """
                )
            finally:
                browser.close()

    with ThreadPoolExecutor(max_workers=1) as executor:
        results = executor.submit(run_browser_test).result()

    violations = results["violations"]

    details = [
        f"{violation['id']}: {violation['help']} "
        f"({len(violation['nodes'])} elements)"
        for violation in violations
    ]

    self.assertEqual(
        violations,
        [],
        "Accessibility violations found:\n"
        + "\n".join(details),
    )


    def test_homepage_accessibility(self):
        self.check_accessibility(
            reverse("home")
        )

    def test_login_accessibility(self):
        self.check_accessibility(
            reverse("login")
        )

    def test_results_accessibility(self):
        self.check_accessibility(
            reverse("scan_results", args=[self.scan.id]),
            authenticated=True,
        )

    def test_history_accessibility(self):
        self.check_accessibility(
            reverse("scan_history"),
            authenticated=True,
        )

    def test_results_accessibility(self):
        self.login()
        self.check_accessibility(
            reverse(
                "scan_results",
                args=[self.scan.id]
            )
        )


class ScannerSecurityTests(SimpleTestCase):

    def test_rejects_localhost(self):
        self.assertFalse(
            is_safe_browser_request("http://localhost")
        )

    def test_rejects_loopback_ip(self):
        self.assertFalse(
            is_safe_browser_request("http://127.0.0.1")
        )

    def test_rejects_private_ip(self):
        self.assertFalse(
            is_safe_browser_request("http://192.168.1.1")
        )

    def test_rejects_nonstandard_port(self):
        self.assertFalse(
            is_safe_browser_request("https://example.com:8080")
        )

    def test_rejects_unsupported_protocol(self):
        self.assertFalse(
            is_safe_browser_request("ftp://example.com")
        )

    @patch("scanner.accessibility.socket.getaddrinfo")
    def test_rejects_domain_resolving_to_private_ip(self, mock_dns):
        mock_dns.return_value = [
            (2, 1, 6, "",("10.0.0.5", 443))
        ]
        self.assertFalse(
            is_safe_browser_request("https://example.com")
        )

    @patch("scanner.accessibility.socket.getaddrinfo")
    def test_accepts_public_domain(self, mock_dns):
        mock_dns.return_value = [
            (2, 1, 6, "", ("93.184.215.14", 443))
        ]
        self.assertTrue(
            is_safe_browser_request("https://example.com")
        )


class ScanComparisonTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="comparison_user",
            password="TestPassword123!"
        )
        self.other_user = User.objects.create_user(
            username="other_comparison_user",
            password="TestPassword456!"
        )
        self.old_scan = self.create_scan(
            user=self.user,
            url="https://example.com",
            critical=3,
            serious=4,
            moderate=2,
            minor=1,
        )
        self.new_scan = self.create_scan(
            user=self.user,
            url="https://example.com/",
            critical=1,
            serious=2,
            moderate=1,
            minor=1,
        )
        self.other_site_scan = self.create_scan(
            user=self.user,
            url="https://example.org",
            critical=5,
        )
        self.other_user_scan = self.create_scan(
            user=self.other_user,
            url="https://example.com",
            critical=7,
        )
        self.url = reverse("scan_comparison", args=[self.new_scan.id])

    def create_scan(
        self,
        user,
        url,
        critical=0,
        serious=0,
        moderate=0,
        minor=0,
        status="completed",
    ):
        return Scan.objects.create(
            user=user,
            url=url,
            status=status,
            results={
                "summary": {
                    "critical": critical,
                    "serious": serious,
                    "moderate": moderate,
                    "minor": minor,
                    "passed": 0,
                    "manual_review": 0,
                },
                "violations": [],
                "passes": [],
                "incomplete": [],
            },
        )

    def test_guest_cannot_access_comparison(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response.url)

    def test_user_can_access_own_comparison(self):
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "scanner/comparison.html")

    def test_user_cannot_access_another_users_scan(self):
        self.client.force_login(self.user)
        url = reverse("scan_comparison", args=[self.other_user_scan.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    def test_only_matching_website_scans_included(self):
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        matching_scans = response.context["matching_scans"]
        scan_ids = [scan.id for scan in matching_scans]
        self.assertEqual(scan_ids, [self.old_scan.id, self.new_scan.id])
        self.assertNotIn(self.other_site_scan.id, scan_ids)
        self.assertNotIn(self.other_user_scan.id, scan_ids)

    def test_chart_data_contains_correct_values(self):
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        chart_data = response.context["chart_data"]
        self.assertEqual(len(chart_data), 2)
        self.assertEqual(chart_data[0]["total"], 10)
        self.assertEqual(chart_data[1]["total"], 5)
        self.assertEqual(chart_data[0]["critical"], 3)
        self.assertEqual(chart_data[1]["critical"], 1)

    def test_comparison_calculates_improvement(self):
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        comparison = response.context["comparison"]
        self.assertEqual(comparison["before_total"], 10)
        self.assertEqual(comparison["after_total"], 5)
        self.assertEqual(comparison["difference"], -5)
        self.assertEqual(comparison["trend"], "Improving")
        self.assertEqual(comparison["categories"]["critical"]["difference"], -2)

    def test_comparison_calculates_regression(self):
        self.client.force_login(self.user)
        worse_scan = self.create_scan(
            user=self.user,
            url="https://example.com",
            critical=5,
            serious=5,
            moderate=3,
            minor=2,
        )

        response = self.client.get(
            self.url,
            {
                "first": self.new_scan.id,
                "second": worse_scan.id,
            }
        )
        comparison = response.context["comparison"]
        self.assertEqual(comparison["before_total"], 5)
        self.assertEqual(comparison["after_total"], 15)
        self.assertEqual(comparison["difference"], 10)
        self.assertEqual(comparison["trend"], "More findings")

    def test_rejects_same_scan_selection(self):
        self.client.force_login(self.user)
        response = self.client.get(
            self.url,
            {
                "first": self.new_scan.id,
                "second": self.new_scan.id,
            }
        )
        self.assertIsNone(response.context["comparison"])
        self.assertIsNotNone(response.context["selection_error"])

    def test_rejects_reversed_scan_order(self):
        self.client.force_login(self.user)
        response = self.client.get(
            self.url,
            {
                "first": self.new_scan.id,
                "second": self.old_scan.id,
            }
        )
        self.assertIsNone(response.context["comparison"])
        self.assertIsNotNone(response.context["selection_error"])

    def test_rejects_invalid_scan_id(self):
        self.client.force_login(self.user)
        response = self.client.get(
            self.url,
            {
                "first": "invalid",
                "second": self.new_scan.id,
            }
        )
        self.assertIsNone(response.context["comparison"])
        self.assertIsNotNone(response.context["selection_error"])

    def test_cannot_select_another_users_scan(self):
        self.client.force_login(self.user)
        response = self.client.get(
            self.url,
            {
                "first": self.old_scan.id,
                "second": self.other_user_scan.id,
            }
        )
        self.assertIsNone(response.context["comparison"])
        self.assertIsNotNone(response.context["selection_error"])

    def test_cannot_select_different_website(self):
        self.client.force_login(self.user)
        response = self.client.get(
            self.url,
            {
                "first": self.old_scan.id,
                "second": self.other_site_scan.id,
            }
        )
        self.assertIsNone(response.context["comparison"])
        self.assertIsNotNone(response.context["selection_error"])

    def test_failed_scan_cannot_be_compared(self):
        self.client.force_login(self.user)
        failed_scan = self.create_scan(
            user=self.user,
            url="https://example.com",
            status="failed",
        )
        url = reverse("scan_comparison", args=[failed_scan.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)


    def test_history_comparison_availability(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("scan_history"))
        self.assertEqual(response.status_code, 200)
        scans = {scan.id: scan for scan in response.context["scans"]}
        self.assertTrue(scans[self.old_scan.id].can_compare) # both scans of example.com can be compared
        self.assertTrue(scans[self.new_scan.id].can_compare)
        self.assertFalse(scans[self.other_site_scan.id].can_compare)  # only one scan exists for example.org
        self.assertNotIn(self.other_user_scan.id, scans)  # another user's scan must not appear
